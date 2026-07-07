#!/usr/bin/env bash
#
# Build the frontend static-site release artifact.
#
# Runs from the repo root on an ubuntu-24.04 runner or container (or a dev box
# with node/npm already installed - this script does not install node itself).
# Produces a tarball of the built assets, ready for the deploy wrapper to
# unpack behind Caddy at the domain root.
#
# Output: dist/frontend-<sha>.tar.gz ; prints the tarball's sha256 on stdout.
set -euo pipefail

# Release identity = the current commit, derived here (not passed in) so the artifact
# filename always reflects the exact tree being built.
sha="$(git rev-parse HEAD)"

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
root="$build/frontend"

# 1. Reproducible install (npm ci, not npm install) then the production build.
#    No --base: the site is served from the domain root. No VITE_* env: a
#    production build defaults to the live API.
( cd frontend && npm ci && npm run build )

# 2. Stage the built assets as the tarball's single top-level directory. The
#    deploy wrapper depends on this layout exactly: frontend/index.html,
#    frontend/assets/..., with nothing else alongside them.
install -d "$root"
cp -a frontend/dist/. "$root/"

# 3. Tar and report the digest the pipeline passes to the wrapper's `upload`.
install -d dist
tar -C "$build" -czf "dist/frontend-$sha.tar.gz" frontend
sha256sum "dist/frontend-$sha.tar.gz" | cut -d' ' -f1
