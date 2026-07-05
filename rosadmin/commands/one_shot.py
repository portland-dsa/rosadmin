"""
Manual-testing commands that run against real external backends.

Most create or inspect a clearly-marked Google Workspace test group driven by
the ``TEST_*`` fixture constants below; the outlier is ``sso-reachability``,
which probes the botonio SSO socket rather than Google. These are intentionally
noisy (real API calls, real propagation delays) and are meant to be run by hand,
not in automated pipelines. (They're in CI/CD right now because fuck it we ball
tho)
"""

from __future__ import annotations

import os
from typing import Annotated

from alive_progress import alive_bar
from cyclopts import App, Parameter
from rich import print as rprint
from rich.rule import Rule

from googleapiclient.errors import HttpError

from rosadmin.auth import get_credentials
from rosadmin.google_group import GoogleGroup, GoogleGroupBuilder
from rosadmin.sso import (
    SsoUnreachable,
    check_reachable,
    sso_bearer,
    sso_settings_from_env,
)

one_shot_app = App(
    name="one-shot", help="Throwaway commands for development and manual testing."
)

TEST_GROUP_EMAIL = "i-made-a-test-group@pdx-dsa.org"
TEST_GROUP_NAME = "A stupid Script Test Group"
TEST_GROUP_DESCRIPTION = "A stupid test security group"
TEST_MEMBER_EMAIL = "admin@pdx-dsa.org"
#: The Workspace user the service account impersonates for these test commands.
TEST_IMPERSONATION_SUBJECT = "admin@pdx-dsa.org"


@one_shot_app.command(name="test-group-lifecycle")
async def one_shot_test_group_lifecycle(
    delete_at_end: Annotated[
        bool,
        Parameter(
            help="Delete the test group after the lifecycle completes. Pass --no-delete-at-end to keep it for inspection via check-test-group."
        ),
    ] = True,
) -> None:
    """Create a test security group, configure its settings, add a member, then delete.

    Uses ``replace_if_exists`` so a leftover group from a previous failed run
    doesn't block the test. Pass ``--delete-at-end=False`` to keep the group for
    inspection; follow up with ``check-test-group`` to verify it, then
    ``delete-test-group`` to clean up.
    """

    creds = get_credentials(TEST_IMPERSONATION_SUBJECT)

    with alive_bar(4, title="test-group-lifecycle") as bar:
        bar.text("Creating and configuring group")
        group = await (
            GoogleGroupBuilder()
            .email(TEST_GROUP_EMAIL)
            .name(TEST_GROUP_NAME)
            .description(TEST_GROUP_DESCRIPTION)
            .secure_defaults()
            .replace_if_exists()
            .build_remote(creds)
        )
        bar()

        bar.text("Adding member")
        await group.add_member(TEST_MEMBER_EMAIL)
        bar()

        bar.text("Collecting group info")
        members = await group.list_members()
        settings = await group.get_settings()
        labels = await group.get_labels()
        bar()

        bar.text("Deleting group" if delete_at_end else "Keeping group by request")
        if delete_at_end:
            await group.delete()
        bar()

    rprint(Rule("Members"))
    rprint(members)

    rprint(Rule("Settings"))
    rprint(settings)

    rprint(Rule("Labels"))
    rprint(labels)


@one_shot_app.command(name="delete-test-group")
async def one_shot_delete_test_group() -> None:
    """Delete the test group if it exists."""

    creds = get_credentials(TEST_IMPERSONATION_SUBJECT)
    try:
        group = await GoogleGroup.from_remote(TEST_GROUP_EMAIL, creds)
    except HttpError as e:
        if e.status_code == 404:
            rprint(f"[yellow]Not found:[/yellow] {TEST_GROUP_EMAIL}")
            return
        raise

    await group.delete()
    rprint(f"[green]Deleted:[/green] {TEST_GROUP_EMAIL}")


@one_shot_app.command(name="check-test-group")
async def one_shot_check_test_group() -> None:
    """Check whether the test group currently exists in Google Workspace.

    Exists so when passing ``--no-delete-at-end``, you can
    verify that works without access to the actual Workspace admin console.
    """

    creds = get_credentials(TEST_IMPERSONATION_SUBJECT)
    try:
        group = await GoogleGroup.from_remote(TEST_GROUP_EMAIL, creds)
    except HttpError as e:
        if e.status_code == 404:
            rprint(f"[yellow]Not found:[/yellow] {TEST_GROUP_EMAIL}")
            return
        raise

    members = await group.list_members()
    settings = await group.get_settings()
    labels = await group.get_labels()

    rprint(f"[green]Exists:[/green] {TEST_GROUP_EMAIL}")

    rprint(Rule("Members"))
    rprint(members)

    rprint(Rule("Settings"))
    rprint(settings)

    rprint(Rule("Labels"))
    rprint(labels)


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
