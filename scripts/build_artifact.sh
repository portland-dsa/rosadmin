#!/usr/bin/env bash
#
# Build the relocatable, self-contained rosadmin release artifact.
#
# Runs on an ubuntu-24.04 runner or container - NOT the Windows dev box. Produces a
# tarball that untars anywhere and runs `python -m rosadmin serve` with no network
# install: it embeds a real copy of a standalone Python 3.13 with the app installed
# straight into it (no venv), and python-build-standalone finds its own stdlib relative
# to the binary, so the tree runs from any path.
#
# Output: dist/rosadmin-<sha>.tar.gz ; prints the tarball's sha256 on stdout.
set -euo pipefail

# Release identity = the current commit, derived here (not passed in) so the artifact
# filename always reflects the exact tree being built.
sha="$(git rev-parse HEAD)"
py="3.13"

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
root="$build/rosadmin"
install -d "$root/python"

# 1. Copy a standalone Python 3.13 INTO the artifact as a REAL directory. `uv python find`
#    returns a path with a symlinked component, so resolve it first (readlink -f) and copy
#    the install root's CONTENTS (the trailing /. dereferences the top-level link, while -a
#    preserves the interpreter's own internal links). The box needs no Python of its own.
uv python install "$py"
py_install="$(dirname "$(dirname "$(readlink -f "$(uv python find "$py")")")")"
cp -a "$py_install/." "$root/python/"

# 2. Install rosadmin and its dependencies straight into the bundled interpreter (no venv,
#    so nothing symlinks back to the build machine). uv flags its managed pythons with a
#    "do not modify me" marker; drop it first - this copy is now our app's dedicated runtime,
#    ours to install into - then `--system` permits the non-venv install.
#    Any further arguments are extra local packages baked into this flavor of the
#    artifact - the staging build passes ./admintools so the operator admin socket
#    exists there, and a production build passes nothing, which is what makes the
#    admin surface absent-by-packaging rather than merely off.
#    `--no-sources` matters: admintools pins rosadmin as a workspace member, and
#    workspace members always install editable - a stub pointing at this runner's
#    checkout. Ignoring the sources table lets the `.` on the command line satisfy
#    that dependency as a real wheel instead.
rm -f "$root/python/lib/python$py/EXTERNALLY-MANAGED"
uv pip install --python "$root/python/bin/python3" --system --no-cache --no-sources . "$@"

# 3. Refuse to ship an editable stub: a finder in site-packages would reach back to
#    this runner's checkout instead of carrying the code, and the path dies on the box.
if find "$root/python" -name '__editable__*' | grep -q .; then
    echo "ERROR: editable install baked into the artifact:" >&2
    find "$root/python" -name '__editable__*' >&2
    exit 1
fi

# 4. Precompile bytecode so the read-only tree never attempts a .pyc write at runtime.
"$root/python/bin/python3" -m compileall -q "$root/python" || true

# 5. Manifest of every file's sha256 (excluding the manifest itself) for the unit's
#    boot-time integrity check. Paths are relative to the artifact root.
( cd "$root" && find . -type f ! -name MANIFEST.sha256 -print0 \
    | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )

# 6. Tar and report the digest the pipeline passes to the wrapper's `upload`.
install -d dist
tar -C "$build" -czf "dist/rosadmin-$sha.tar.gz" rosadmin
sha256sum "dist/rosadmin-$sha.tar.gz" | cut -d' ' -f1
