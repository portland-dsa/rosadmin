"""Run the tool straight from a source checkout, from the repo root:

    uv run --project scripts/rosadmin_deploy scripts/rosadmin_deploy <verb> ...

The bundled .pyz gets its own entry point from zipapp; this is the equivalent for the tree.

Run as a package submodule (``-m rosadmin_deploy``) the relative import resolves. Run as a
loose directory (``uv run .../rosadmin_deploy``) there is no parent package, so put the
package's parent on ``sys.path`` and import it absolutely instead.
"""

if __package__:
    from .cli import main
else:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rosadmin_deploy.cli import main

if __name__ == "__main__":
    main()
