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

from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import parse_map
from rosadmin.mock_st.server import create_app

BASE = "http://mock.test"


def _call(
    app: Any, action: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]
) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await action(client)

    return asyncio.run(go())


@given('a controllable mock with roster "{spec}"')
def step_controllable_roster(context, spec):
    context.app = create_app(parse_map(spec), controllable=True)


@given('a read-only mock with roster "{spec}"')
def step_read_only_roster(context, spec):
    context.app = create_app(parse_map(spec))


@when('Ralsei sets "{email}" to persona "{persona}"')
@when('Spamton sets "{email}" to persona "{persona}"')
def step_set(context, email, persona):
    context.response = _call(
        context.app,
        lambda c: c.put("/_control/member", json={"email": email, "persona": persona}),
    )


@when('Susie deletes "{email}"')
def step_delete(context, email):
    # httpx's `delete()` shorthand does not accept a body; `request()` does.
    context.response = _call(
        context.app,
        lambda c: c.request("DELETE", "/_control/member", json={"email": email}),
    )


@then("the control response is {status:d}")
def step_status(context, status):
    assert context.response.status_code == status, context.response.text


@then('the control response is member "{email}" with id {expected:d}')
def step_response_member(context, email, expected):
    body = context.response.json()
    assert body["email"] == email
    assert body["id"] == expected


@then('a read of "{email}" shows persona "{persona}"')
def step_read_persona(context, email, persona):
    response = _call(context.app, lambda c: c.get("/users", params={"email": email}))
    rows = response.json()["data"]
    assert len(rows) == 1, rows
    expected = Persona.parse(persona)
    assert expected is not None, persona
    assert rows[0] == expected.user_json(rows[0]["id"], email)


@then('the roster no longer lists "{email}"')
def step_absent(context, email):
    response = _call(context.app, lambda c: c.get("/users", params={"email": email}))
    assert response.json()["data"] == []
