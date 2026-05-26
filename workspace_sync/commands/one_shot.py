from __future__ import annotations

from alive_progress import alive_bar
from cyclopts import App
from googleapiclient.errors import HttpError
from rich import print as rprint
from rich.rule import Rule

from workspace_sync.auth import get_credentials
from workspace_sync.google_group import GoogleGroup, GoogleGroupBuilder

one_shot_app = App(
    name="one-shot", help="Throwaway commands for development and manual testing."
)

TEST_GROUP_EMAIL = "i-made-a-test-group@portlanddsa.org"
TEST_GROUP_NAME = "A stupid Script Test Group"
TEST_GROUP_DESCRIPTION = "A stupid test security group"
TEST_MEMBER_EMAIL = "info@portlanddsa.org"
TEST_IMPERSONATION_SUBJECT = "info@portlanddsa.org"


@one_shot_app.command(name="test-group-lifecycle")
async def one_shot_test_group_lifecycle(delete_at_end: bool = True) -> None:
    """Create a test security group, configure its settings, add a member, then delete."""

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
    """Check whether the test group currently exists in Google Workspace."""

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
