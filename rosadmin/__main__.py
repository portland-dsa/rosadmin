"""Entry point for `python -m rosadmin`.

The deployed artifact runs the service this way - `python -m rosadmin serve` - rather than
through the installed `rosadmin` console script, whose shebang would carry the build
machine's absolute interpreter path and break once the release tree is relocated.
"""

from rosadmin.cli import app

if __name__ == "__main__":
    app()
