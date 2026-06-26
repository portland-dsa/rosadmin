# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import httplib2
from behave import given, then, when
from googleapiclient import discovery
from googleapiclient.http import HttpMockSequence

import rosadmin.google_group as gg
from rosadmin.google_group import (
    SECURE_SETTINGS,
    SECURITY_LABEL,
    GoogleGroup,
    GoogleGroupBuilder,
)

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials

OK: dict[str, str] = {"status": "200"}
NOT_FOUND: dict[str, str] = {"status": "404"}

# The monkeypatched _build_services ignores creds; this stands in for the argument.
NO_CREDS = cast("Credentials", None)

Pair = tuple[dict[str, str], dict[str, Any]]


def _install(context, pairs: list[Pair]) -> None:
    """Point GoogleGroup's service builder at a shared HttpMockSequence for this scenario."""
    seq = HttpMockSequence([(headers, json.dumps(body)) for headers, body in pairs])

    def fake_build_services(creds: Any = None, http: Any = None):
        def build(name: str, version: str):
            return discovery.build(
                name,
                version,
                http=cast(httplib2.Http, seq),
                cache_discovery=False,
                static_discovery=True,
            )

        return (
            build("admin", "directory_v1"),
            build("cloudidentity", "v1"),
            build("groupssettings", "v1"),
        )

    context.orig_build = gg._build_services
    gg._build_services = fake_build_services


@given("the Google APIs accept a new group after one consistency retry")
def step_accept_new(context):
    _install(
        context,
        [
            (OK, {"id": "g1", "email": "kris-test@example.com", "name": "Kris Test"}),
            (NOT_FOUND, {}),  # _await_creation: settings.get 404 -> retry
            (OK, {}),  # _await_creation: settings.get ok
            (OK, {}),  # _await_creation: identity.get ok
            (OK, {}),  # _raw_configure: identity.patch
            (OK, {}),  # _raw_configure: settings.patch
            (
                OK,
                cast(dict[str, Any], SECURE_SETTINGS),
            ),  # _await_settings: settings match
            (
                OK,
                {"labels": SECURITY_LABEL.get("labels")},
            ),  # _await_settings: labels match
        ],
    )


@given('the Google APIs describe an existing group with id "{gid}" and name "{name}"')
def step_existing(context, gid, name):
    _install(
        context,
        [
            (OK, {"id": gid, "name": name, "description": "desc"}),  # admin.get
            (OK, {}),  # settings.get
            (OK, {"labels": {}}),  # identity.get
        ],
    )


@given("the Google APIs confirm a member after one visibility retry")
def step_member_visible(context):
    _install(
        context,
        [
            (
                OK,
                {"id": "g3", "name": "Group", "description": "desc"},
            ),  # from_remote admin.get
            (OK, {}),  # from_remote settings.get
            (OK, {"labels": {}}),  # from_remote identity.get
            (OK, {}),  # _raw_add_member: members.insert
            (OK, {"isMember": False}),  # _await_member_add: not yet -> retry
            (OK, {"isMember": True}),  # _await_member_add: visible
        ],
    )


@given("the Google APIs report the group gone after one deletion retry")
def step_group_gone(context):
    _install(
        context,
        [
            (
                OK,
                {"id": "g4", "name": "Group", "description": "desc"},
            ),  # from_remote admin.get
            (OK, {}),  # from_remote settings.get
            (OK, {"labels": {}}),  # from_remote identity.get
            (OK, {"id": "g4"}),  # _raw_delete: admin.get (exists)
            (OK, {}),  # _raw_delete: admin.delete
            (OK, {}),  # _await_deletion: still exists -> retry
            (NOT_FOUND, {}),  # _await_deletion: gone
        ],
    )


@when('Ralsei builds the "{email}" group')
def step_build(context, email):
    async def go() -> GoogleGroup:
        return await (
            GoogleGroupBuilder()
            .email(email)
            .name("Kris Test")
            .description("offline")
            .secure_defaults()
            .build_remote(NO_CREDS)
        )

    context.group = asyncio.run(go())


@when('Ralsei hydrates "{email}"')
def step_hydrate(context, email):
    context.group = asyncio.run(GoogleGroup.from_remote(email, NO_CREDS))


@when('Ralsei adds "{email}" to the group')
def step_add(context, email):
    async def go() -> None:
        group = await GoogleGroup.from_remote("group@example.com", NO_CREDS)
        await group.add_member(email)

    asyncio.run(go())
    context.ok = True


@when("Susie deletes the group")
def step_delete(context):
    async def go() -> None:
        group = await GoogleGroup.from_remote("group@example.com", NO_CREDS)
        await group.delete()

    asyncio.run(go())
    context.ok = True


@then('the built group has id "{gid}"')
def step_built_id(context, gid):
    assert context.group.id == gid


@then('the hydrated group has id "{gid}" and name "{name}"')
def step_hydrated(context, gid, name):
    assert context.group.id == gid
    assert context.group.name == name


@then("the operation succeeds")
def step_ok(context):
    assert context.ok is True
