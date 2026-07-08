"""The full `GoogleGroup` arc against a real Workspace tenant.

Exercises the three-API dance end to end - create, configure, add a member,
list it back, remove it, and delete - which no mock can stand in for. This is
the only place that dance is proven live, so it stays a single pytest rather
than a behave scenario: cleanup wants a plain `finally`, not a step file, and
CI wants one marker to gate the whole thing behind a real service-account key.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from rosadmin.auth import get_credentials
from rosadmin.google_group import GoogleGroup, GoogleGroupBuilder, _poll_until

TEST_GROUP_EMAIL = "i-made-a-test-group@pdx-dsa.org"
TEST_GROUP_NAME = "A stupid Script Test Group"
TEST_GROUP_DESCRIPTION = "A stupid test security group"
TEST_MEMBER_EMAIL = "admin@pdx-dsa.org"
#: The Workspace user the service account impersonates for this test.
TEST_IMPERSONATION_SUBJECT = "admin@pdx-dsa.org"

pytestmark = [
    pytest.mark.credentials,
    pytest.mark.skipif(
        not (os.environ.get("CREDENTIALS_JSON") or os.environ.get("CREDENTIALS_FILE")),
        reason="needs a real DWD service-account key via CREDENTIALS_JSON or CREDENTIALS_FILE",
    ),
]


async def test_group_lifecycle_against_real_workspace() -> None:
    creds = get_credentials(TEST_IMPERSONATION_SUBJECT)

    group = await (
        GoogleGroupBuilder()
        .email(TEST_GROUP_EMAIL)
        .name(TEST_GROUP_NAME)
        .description(TEST_GROUP_DESCRIPTION)
        .secure_defaults()
        .replace_if_exists()
        .build_remote(creds)
    )

    # A membership mutation's response is already telling, so add/remove
    # do not poll; the *listing* is what can lag behind it. This test is the
    # one reader that insists on seeing the change listed, so it owns the
    # wait - `_poll_until` raises PropagationTimeout if the change never shows.
    def _listed() -> bool:
        return any(m.email == TEST_MEMBER_EMAIL for m in group._raw_list_members())

    try:
        await group.add_member(TEST_MEMBER_EMAIL)
        await asyncio.to_thread(_poll_until, _listed)

        await group.remove_member(TEST_MEMBER_EMAIL)
        await asyncio.to_thread(_poll_until, lambda: not _listed())

        settings = await group.get_settings()
        assert settings.get("whoCanJoin") == "INVITED_CAN_JOIN"

        labels = await group.get_labels()
        assert "cloudidentity.googleapis.com/groups.security" in labels

        fetched = await GoogleGroup.from_remote(TEST_GROUP_EMAIL, creds)
        assert fetched.email == TEST_GROUP_EMAIL
    finally:
        await group.delete()
