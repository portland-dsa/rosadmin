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

from rosadmin.membership.solidarity_tech.decode import decode_user
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
@when('Susie sets "{email}" to persona "{persona}"')
@when('Spamton sets "{email}" to persona "{persona}"')
def step_set(context, email, persona):
    context.response = _call(
        context.app,
        lambda c: c.put("/_control/member", json={"email": email, "persona": persona}),
    )


@when('Ralsei sets "{email}" to persona "{persona}" with the discord id {discord_id}')
def step_set_with_id(context, email, persona, discord_id):
    context.response = _call(
        context.app,
        lambda c: c.put(
            "/_control/member",
            json={"email": email, "persona": persona, "discord_id": discord_id},
        ),
    )


@when('Susie deletes "{email}"')
def step_delete(context, email):
    # httpx's `delete()` shorthand does not accept a body; `request()` does.
    context.response = _call(
        context.app,
        lambda c: c.request("DELETE", "/_control/member", json={"email": email}),
    )


@when('Susie expires "{email}"')
def step_expire(context, email):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/standing", json={"email": email, "standing": "lapsed"}
        ),
    )


@when('Ralsei restores "{email}" to good standing')
def step_restore(context, email):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/standing",
            json={"email": email, "standing": "good_standing"},
        ),
    )


@when('Spamton sets "{email}" to an unrecognized standing')
def step_unrecognized_standing(context, email):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/standing",
            json={"email": email, "standing": "certified_kromer_holder"},
        ),
    )


@given('Ralsei grants "{email}" the "{field}" body "{label}"')
@when('Ralsei grants "{email}" the "{field}" body "{label}"')
def step_grant_leadership(context, email, field, label):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/leadership",
            json={"email": email, "field": field, "label": label, "present": True},
        ),
    )


@when('Susie revokes "{email}" from the "{field}" body "{label}"')
def step_revoke_leadership(context, email, field, label):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/leadership",
            json={"email": email, "field": field, "label": label, "present": False},
        ),
    )


@when('Spamton grants "{email}" an unrecognized leadership field')
def step_unrecognized_field(context, email):
    context.response = _call(
        context.app,
        lambda c: c.post(
            "/_control/member/leadership",
            json={
                "email": email,
                "field": "spamton-nft-portfolio",
                "label": "Big Shot",
                "present": True,
            },
        ),
    )


def _record(context, email: str) -> dict[str, Any]:
    response = _call(context.app, lambda c: c.get("/users", params={"email": email}))
    rows = response.json()["data"]
    assert len(rows) == 1, rows
    return rows[0]


@then('"{email}" decodes with standing "{standing}"')
def step_decodes_standing(context, email, standing):
    member = decode_user(_record(context, email))
    assert member.standing.value == standing


@then('"{email}" decodes as a leader of "{body}"')
def step_decodes_leader_of(context, email, body):
    member = decode_user(_record(context, email))
    assert any(lead.name == body for lead in member.leads), member.leads


@then('"{email}" decodes as leading no bodies')
def step_decodes_no_bodies(context, email):
    member = decode_user(_record(context, email))
    assert member.leads == frozenset()


@then('"{email}" decodes with the chapter-leader flag set')
def step_decodes_flag_set(context, email):
    assert decode_user(_record(context, email)).is_chapter_leader is True


@then('"{email}" decodes with the chapter-leader flag clear')
def step_decodes_flag_clear(context, email):
    assert decode_user(_record(context, email)).is_chapter_leader is False


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


@then('"{email}" decodes with the discord id {discord_id:d}')
def step_decodes_discord_id(context, email, discord_id):
    member = decode_user(_record(context, email))
    assert member.discord_id == discord_id, member.discord_id
