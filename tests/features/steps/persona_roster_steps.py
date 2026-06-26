# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from behave import given, then, when

from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.source import Member, Standing
from rosadmin.mock_st.roster import parse_map
from rosadmin.mock_st.server import create_app

BASE = "http://mock.test"


def _read(app: Any, action: Callable[[SolidarityTechClient], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        transport = httpx.ASGITransport(app=app)
        http = httpx.AsyncClient(transport=transport, base_url=BASE)
        client = SolidarityTechClient(token="t0ken", base_url=BASE, client=http)
        try:
            return await action(client)
        finally:
            await client.aclose()

    return asyncio.run(go())


# The empty-roster step is registered as a literal so behave resolves the "" case
# without the parameterized pattern (whose {spec} field does not match an empty string).
@given('a persona roster ""')
def step_roster_empty(context):
    context.app = create_app([])


@given('a persona roster "{spec}"')
def step_roster(context, spec):
    context.app = create_app(parse_map(spec))


@when("Ralsei reads the roster")
def step_read(context):
    context.members = _read(context.app, lambda c: c.list_members())


@when('Ralsei looks up the member "{email}"')
def step_lookup(context, email):
    context.result = _read(context.app, lambda c: c.find_by_email(email))


@then("the roster has {count:d} member")
@then("the roster has {count:d} members")
def step_count(context, count):
    assert len(context.members) == count


@then("the lookup returns a member in good standing")
def step_lookup_member(context):
    result: Member | None = context.result
    assert result is not None
    assert result.standing is Standing.GOOD_STANDING


@then("the lookup returns no member")
def step_lookup_none(context):
    assert context.result is None
