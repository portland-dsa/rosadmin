"""
Manual-testing commands that run against real external backends.

``sso-reachability`` probes the botonio SSO socket rather than Google. It's
intentionally noisy (a real socket call) and meant to be run by hand, not in
automated pipelines.
"""

from __future__ import annotations

import os

from cyclopts import App
from rich import print as rprint
from rich.rule import Rule

from rosadmin.sso import (
    SsoUnreachable,
    check_reachable,
    sso_bearer,
    sso_settings_from_env,
)

one_shot_app = App(
    name="one-shot", help="Throwaway commands for development and manual testing."
)


@one_shot_app.command(name="sso-reachability")
async def one_shot_sso_reachability() -> None:
    """Probe the botonio SSO socket with one authenticated ``/sso/begin``.

    Proves the socket path, the shared group, the bearer, and both of the bot's
    enable switches are wired - before the relay that uses them exists. It mints
    no session and completes no login; a ``200`` carrying an authorize URL is the
    whole success condition. Configure it through ``BOTONIO_SSO_SOCKET_PATH`` and
    the bearer (``$CREDENTIALS_DIRECTORY/botonio_sso_bearer`` or
    ``BOTONIO_SSO_BEARER``), and run it on the box or inside WSL, where the socket
    lives.
    """

    settings = sso_settings_from_env(os.environ)
    bearer = sso_bearer(os.environ)
    try:
        reachable = await check_reachable(settings, bearer)
    except SsoUnreachable as error:
        rprint(f"[red]Unreachable:[/red] {error}")
        raise SystemExit(1) from error

    rprint(
        f"[green]Reachable:[/green] botonio answered /sso/begin over {settings.socket_path}"
    )
    rprint(Rule("authorize_url"))
    rprint(reachable.authorize_url)
