# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
import os

import httpx
from behave import then, when

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.service import create_app
from rosadmin.sso import DiscordUserId
from rosadmin.web.records import RecordsDirectory
from rosadmin.web.sessions import InMemorySessionStore, Principal
from rosadmin.web.settings import WebSettings

BASE = "https://testserver"


def _query_one(dsn, sql_text, params=()):
    async def go():
        pool = make_pool(dsn)
        await pool.open()
        try:
            async with pool.connection() as conn:
                cursor = await conn.execute(sql_text, params)
                return await cursor.fetchone()
        finally:
            await pool.close()

    return asyncio.run(go())


def _call(context, method, path, **kwargs):
    # `AsyncConnectionPool.open()` binds its scheduler and worker tasks to
    # whatever event loop is running at the time, so a pool cannot outlive the
    # loop that opened it. Each request here gets its own fresh pool, opened
    # and closed inside the same `asyncio.run`, rather than one pool shared
    # across calls on different loops.
    async def go():
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        context.app.state.pool = pool
        context.app.state.directory = RecordsDirectory(pool)
        try:
            transport = httpx.ASGITransport(app=context.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url=BASE,
                cookies={"rosadmin_session": context.token},
            ) as client:
                return await client.request(method, path, **kwargs)
        finally:
            await pool.close()

    return asyncio.run(go())


@when('Ralsei is logged in against records as "{email}"')
def step_login(context, email):
    row = _query_one(
        context.db.app_dsn,
        "SELECT discord_user_id FROM members WHERE email = %s",
        (email,),
    )
    assert row is not None, f"no seeded member for {email!r}"
    (discord_id,) = row

    store = InMemorySessionStore()
    context.token = asyncio.run(
        store.create(Principal(discord_id=DiscordUserId(str(discord_id))))
    )
    context.app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None),
        session_store=store,
        audit_sink=RecordingAuditSink(),
    )


@when("Ralsei fetches his groups from records")
def step_fetch(context):
    context.response = _call(context, "GET", "/api/me/groups")


@when('Ralsei searches records for "{email}"')
def step_search(context, email):
    context.response = _call(
        context, "POST", "/api/members/search", json={"email": email}
    )


@when("a client attempts to add a member without mutations wired")
def step_add_unwired(context):
    context.response = _call(
        context,
        "POST",
        "/api/groups/00000000-0000-0000-0000-000000000000/members",
        json={"member_id": "00000000-0000-0000-0000-000000000000"},
    )


@then("records lists {count:d} group")
@then("records lists {count:d} groups")
def step_group_count(context, count):
    assert len(context.response.json()) == count, context.response.text


@then('one of the returned groups is "{name}" led by "{leader_name}"')
def step_group_led(context, name, leader_name):
    groups = context.response.json()
    group = next(g for g in groups if g["name"] == name)
    assert any(
        m["full_name"] == leader_name and m["role"] == "leader"
        for m in group["members"]
    ), group


@then('the records search status is "{status}"')
def step_search_status(context, status):
    assert context.response.json()["status"] == status, context.response.text


@then("the mutation is refused because mutations are not available")
def step_mutation_refused(context):
    assert context.response.status_code == 501, context.response.text
    assert context.response.headers["content-type"].startswith(
        "application/problem+json"
    )
    assert context.response.json()["code"] == "mutations_not_available"


@when('Ralsei fake-logs-in against records as persona "{persona}"')
def step_fake_login_records(context, persona):
    # The records branch of fake-login resolves the persona through the same
    # map the mock serves, so hand it the Background's spec via the env var.
    previous = os.environ.get("SOLIDARITY_TECH_MOCK_PERSONAS")
    os.environ["SOLIDARITY_TECH_MOCK_PERSONAS"] = context.persona_spec

    def restore():
        if previous is None:
            os.environ.pop("SOLIDARITY_TECH_MOCK_PERSONAS", None)
        else:
            os.environ["SOLIDARITY_TECH_MOCK_PERSONAS"] = previous

    context.add_cleanup(restore)
    context.app = create_app(
        WebSettings(fake_login_enabled=True, allowed_origin=None),
        session_store=InMemorySessionStore(),
        audit_sink=RecordingAuditSink(),
    )
    context.token = ""
    context.response = _call(
        context, "POST", "/api/auth/fake-login", json={"persona": persona}
    )


@then('the fake login answers 200 with display name "{name}"')
def step_fake_login_ok(context, name):
    assert context.response.status_code == 200, context.response.text
    assert context.response.json()["display_name"] == name, context.response.text
    assert context.response.cookies.get("rosadmin_session"), "no session cookie set"


@then('the fake login is refused with "{code}"')
def step_fake_login_refused(context, code):
    assert context.response.status_code == 403, context.response.text
    assert context.response.json()["code"] == code, context.response.text
