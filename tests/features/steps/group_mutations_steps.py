# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
import zlib

import httpx
import psycopg
from behave import given, then, when

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.service import create_app
from rosadmin.sso import DiscordUserId
from rosadmin.web.records import RecordsDirectory, RecordsGroupModify
from rosadmin.web.sessions import InMemorySessionStore, Principal
from rosadmin.web.settings import WebSettings

BASE = "https://testserver"


def _st_id(seed: str) -> int:
    """A deterministic, distinct `st_id` for a seeded fixture, from its key."""
    return zlib.crc32(seed.encode())


def _ensure_context(context) -> None:
    if hasattr(context, "bodies"):
        return
    context.bodies = {}
    context.body_emails = {}
    context.people = {}
    context.discord_ids = {}
    context.members = {}
    context.app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None),
        session_store=InMemorySessionStore(),
        audit_sink=RecordingAuditSink(),
    )
    context.tokens = {}


#: The seeded bodies' types, mirroring the stub roster's pairings; anything
#: else a scenario invents seeds as a Committee.
_BODY_TYPES = {
    "Steering": "Committee",
    "Castle Town": "Committee",
    "Fun Gang Reunion": "Campaign",
}


def _ensure_body(context, body: str, *, linked: bool) -> None:
    _ensure_context(context)
    if body in context.bodies:
        return
    slug = body.lower().replace(" ", "-")
    leader_email = f"{slug}-leaders@example.org" if linked else None
    member_email = f"{slug}-members@example.org" if linked else None
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO leadership_bodies "
            "(name, body_type, leader_google_group_email, member_google_group_email) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (body, _BODY_TYPES.get(body, "Committee"), leader_email, member_email),
        )
        row = cursor.fetchone()
    assert row is not None
    context.bodies[body] = row[0]
    context.body_emails[body] = (leader_email, member_email)


def _ensure_person(context, name: str) -> None:
    _ensure_context(context)
    if name in context.people:
        return
    email = f"{name.lower()}@example.org"
    discord_id = _st_id(f"{name}!discord")
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO members (st_id, email, standing, discord_user_id) "
            "VALUES (%s, %s, 'good_standing', %s) RETURNING id",
            (_st_id(email), email, discord_id),
        )
        row = cursor.fetchone()
    assert row is not None
    context.people[name] = row[0]
    context.discord_ids[name] = discord_id


def _seed_role(context, member_id, body_id, role: str) -> None:
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO body_memberships (member_id, body_id, role) VALUES (%s, %s, %s)",
            (member_id, body_id, role),
        )


def _ensure_member(context, email: str, *, standing: str) -> None:
    _ensure_context(context)
    if email in context.members:
        with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE members SET standing = %s WHERE id = %s",
                (standing, context.members[email]),
            )
        return
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s) "
            "RETURNING id",
            (_st_id(email), email, standing),
        )
        row = cursor.fetchone()
    assert row is not None
    context.members[email] = row[0]


def _target_id(context, target: str):
    """A target named in a step: a plain member (by email) or a persona (by name)."""
    return context.members.get(target, context.people.get(target))


def _login(context, name: str) -> str:
    if name not in context.tokens:
        store: InMemorySessionStore = context.app.state.session_store
        token = asyncio.run(
            store.create(
                Principal(discord_id=DiscordUserId(str(context.discord_ids[name])))
            )
        )
        context.tokens[name] = token
    return context.tokens[name]


def _query(dsn, sql_text, params=()):
    async def go():
        pool = make_pool(dsn)
        await pool.open()
        try:
            async with pool.connection() as conn:
                cursor = await conn.execute(sql_text, params)
                return await cursor.fetchall()
        finally:
            await pool.close()

    return asyncio.run(go())


def _call(context, token: str, method: str, path: str, **kwargs) -> httpx.Response:
    # A fresh pool per call, opened and closed inside one `asyncio.run`:
    # `AsyncConnectionPool.open()` binds to the running loop and cannot outlive it.
    async def go() -> httpx.Response:
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        context.app.state.pool = pool
        context.app.state.directory = RecordsDirectory(pool)
        context.app.state.group_modify = RecordsGroupModify(
            pool, context.app.state.group_sync, context.app.state.audit_sink
        )
        try:
            transport = httpx.ASGITransport(app=context.app)
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE, cookies={"rosadmin_session": token}
            ) as client:
                response = await client.request(method, path, **kwargs)
            # The mirror now runs in a background task spawned after the HTTP
            # response returns; drain it before the pool closes so the sync/
            # audit assertions below still observe the completed mirror.
            await context.app.state.group_modify.drain()
            return response
        finally:
            await pool.close()

    return asyncio.run(go())


@given('{name} leads the linked body "{body}"')
def step_leads_linked(context, name, body):
    _ensure_body(context, body, linked=True)
    _ensure_person(context, name)
    _seed_role(context, context.people[name], context.bodies[body], "leader")


@given('{name} leads the unlinked body "{body}"')
def step_leads_unlinked(context, name, body):
    _ensure_body(context, body, linked=False)
    _ensure_person(context, name)
    _seed_role(context, context.people[name], context.bodies[body], "leader")


@given('{name} leads "{body}"')
def step_leads(context, name, body):
    _ensure_person(context, name)
    _seed_role(context, context.people[name], context.bodies[body], "leader")


@given('{name} is a leader of "{body}"')
def step_is_leader(context, name, body):
    _ensure_person(context, name)
    _seed_role(context, context.people[name], context.bodies[body], "leader")


@given('a good-standing member "{email}"')
def step_good_standing(context, email):
    _ensure_member(context, email, standing="good_standing")


@given('a lapsed member "{email}"')
def step_lapsed(context, email):
    _ensure_member(context, email, standing="lapsed")


@given('"{email}" has the alternate email "{alternate}"')
def step_alternate_email(context, email, alternate):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE members SET alternate_email = %s WHERE id = %s",
            (alternate, context.members[email]),
        )


@given('"{email}" is a member of "{body}"')
def step_seed_membership(context, email, body):
    _seed_role(context, context.members[email], context.bodies[body], "member")


@when('{name} adds "{target}" to the body "{body}"')
def step_add(context, name, target, body):
    token = _login(context, name)
    context.response = _call(
        context,
        token,
        "POST",
        f"/api/groups/{context.bodies[body]}/members",
        json={"member_id": str(_target_id(context, target))},
    )


@when('{name} removes "{target}" from the body "{body}"')
def step_remove(context, name, target, body):
    token = _login(context, name)
    context.response = _call(
        context,
        token,
        "DELETE",
        f"/api/groups/{context.bodies[body]}/members/{_target_id(context, target)}",
    )


def _body_rows(context, body: str) -> set[tuple]:
    return set(
        _query(
            context.db.app_dsn,
            "SELECT member_id, role, added_by, manually_added_at "
            "FROM body_memberships WHERE body_id = %s",
            (context.bodies[body],),
        )
    )


@when('{name} tries to {operation} "{target}" in the body "{body}"')
def step_attempt(context, name, operation, target, body):
    """The refusal outline's single door: snapshot the body's rows, attempt the
    write, and leave the snapshot for the rows-unchanged assertion.

    An unresolvable target (no seeded member or persona by that key) is passed
    through raw - that is the malformed-input case, refused at validation.
    """
    context.snapshot_body = body
    context.snapshot_rows = _body_rows(context, body)
    token = _login(context, name)
    resolved = _target_id(context, target)
    target_id = str(resolved) if resolved is not None else target
    if operation == "add":
        context.response = _call(
            context,
            token,
            "POST",
            f"/api/groups/{context.bodies[body]}/members",
            json={"member_id": target_id},
        )
    elif operation == "remove":
        context.response = _call(
            context,
            token,
            "DELETE",
            f"/api/groups/{context.bodies[body]}/members/{target_id}",
        )
    else:
        raise AssertionError(f"unknown operation {operation!r}")


@then("that body's membership rows are unchanged")
def step_rows_unchanged(context):
    now = _body_rows(context, context.snapshot_body)
    assert now == context.snapshot_rows, (now, context.snapshot_rows)


@then("the response is {status:d}")
def step_status(context, status):
    assert context.response.status_code == status, context.response.text


@then('the response is {status:d} "{code}"')
def step_status_code(context, status, code):
    assert context.response.status_code == status, context.response.text
    assert context.response.json()["code"] == code, context.response.text


@then('"{email}" is a member of "{body}" added by {actor}')
def step_member_added_by(context, email, body, actor):
    rows = _query(
        context.db.app_dsn,
        "SELECT added_by, manually_added_at FROM body_memberships "
        "WHERE member_id = %s AND body_id = %s AND role = 'member'",
        (context.members[email], context.bodies[body]),
    )
    assert len(rows) == 1, rows
    added_by, added_at = rows[0]
    assert added_by == context.people[actor]
    assert added_at is not None


@then('"{email}" is not a member of "{body}"')
def step_not_member(context, email, body):
    rows = _query(
        context.db.app_dsn,
        "SELECT 1 FROM body_memberships "
        "WHERE member_id = %s AND body_id = %s AND role = 'member'",
        (context.members[email], context.bodies[body]),
    )
    assert rows == []


@then('"{name}" still leads "{body}"')
def step_still_leads(context, name, body):
    rows = _query(
        context.db.app_dsn,
        "SELECT 1 FROM body_memberships "
        "WHERE member_id = %s AND body_id = %s AND role = 'leader'",
        (context.people[name], context.bodies[body]),
    )
    assert len(rows) == 1, rows


@then(
    'the sync recorded an "{op}" on "{body}"\'s member group for "{email}" with outcome "{outcome}"'
)
def step_sync_recorded(context, op, body, email, outcome):
    _, member_group_email = context.body_emails[body]
    recorded = context.app.state.group_sync.recorded
    matches = [r for r in recorded if r[0] == op and r[2] == email]
    assert matches, recorded
    _, group_email, _member_email, actual_outcome = matches[-1]
    assert group_email == member_group_email, (group_email, member_group_email)
    assert actual_outcome.value == outcome, actual_outcome


@then("the sync recorded exactly {count:d} call")
@then("the sync recorded exactly {count:d} calls")
def step_call_count(context, count):
    assert len(context.app.state.group_sync.recorded) == count, (
        context.app.state.group_sync.recorded
    )


@then('the mutation audit for "{action}" records "{google_value}"')
def step_audit_detail(context, action, google_value):
    records = [r for r in context.app.state.audit_sink.records if r.action == action]
    assert records, context.app.state.audit_sink.records
    assert records[-1].detail.get("google") == google_value, records[-1].detail


@then('the mutation audit records "{action}"')
def step_audit_recorded(context, action):
    records = context.app.state.audit_sink.records
    assert any(r.action == action for r in records), records
