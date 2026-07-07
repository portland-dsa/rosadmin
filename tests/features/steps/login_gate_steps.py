# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
import logging

import httpx
import psycopg
import respx
from behave import given, then, when

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.membership.source import LeadershipAssessment
from rosadmin.service import create_app
from rosadmin.sso import SigningKeys, SsoConfig, SsoSettings
from rosadmin.web.jti import InMemoryJtiCache
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings
from tests.support.paseto import keypair, sign_assertion

BASE = "https://testserver"

_SETTINGS = SsoSettings(
    iss="botonio",
    aud="rosadmin",
    kid="v1",
    guild_id="42",
    socket_path="/tmp/botonio.sock",
)

#: Fixed rather than derived from the scenario: the `@db` rig truncates the
#: members table before every scenario, so nothing here ever collides with a
#: row from a prior one.
_ST_ID = 90001
_DISCORD_ID = 900001001

#: A raw flag consistent with `assess()`'s own rule, so a seeded row looks
#: like a pull would have produced it rather than an impossible combination.
_FLAGGED = frozenset({LeadershipAssessment.Leader, LeadershipAssessment.EmptyLeader})


class _CapturingHandler(logging.Handler):
    """Collects log records instead of emitting them, so a step can assert on
    what was logged without asserting the exact message text."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _seed(dsn: str, assessment: LeadershipAssessment) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO members "
            "(st_id, email, standing, discord_user_id, is_chapter_leader,"
            " leadership_assessment) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                _ST_ID,
                "gate-subject@example.com",
                "good_standing",
                _DISCORD_ID,
                assessment in _FLAGGED,
                assessment.value,
            ),
        )


@given("a member stored as {assessment_name}")
def step_seed(context, assessment_name):
    _seed(context.db.superuser_dsn, LeadershipAssessment[assessment_name])


@given("no member is stored for the login")
def step_no_member(context) -> None:
    pass  # nothing to seed; gate_lookup finds no row for the Discord id below.


def _build_app(context):
    """Assemble the login app and its botonio mock for a records-gated login.

    A fresh signing key, in-memory session/jti stores, and an assertion whose
    `sub` is the seeded member's Discord id, so the callback's gate lookup finds
    the row the scenario seeded. The audit sink stays on the context so a step
    can assert what was recorded.
    """
    signing_key, raw_pub = keypair()
    assertion = sign_assertion(signing_key, sub=str(_DISCORD_ID))
    context.audit = RecordingAuditSink()
    app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None),
        session_store=InMemorySessionStore(),
        audit_sink=context.audit,
        sso=SsoConfig(
            settings=_SETTINGS,
            bearer="test-bearer",
            keys=SigningKeys({"v1": raw_pub}),
        ),
        jti_cache=InMemoryJtiCache(),
    )
    context.router = respx.mock(assert_all_mocked=False)
    context.router.start()
    context.router.post("http://botonio/sso/begin").mock(
        return_value=httpx.Response(
            200, json={"authorize_url": "https://discord/x", "state": "s1"}
        )
    )
    context.router.post("http://botonio/sso/complete").mock(
        return_value=httpx.Response(200, json={"assertion": assertion})
    )
    return app


def _run_login(context) -> None:
    """Drive `begin` then `callback` over one client, one pool, one event loop.

    `AsyncConnectionPool.open()` binds to the running loop and cannot outlive it,
    so the pool is opened and closed inside the same `asyncio.run` as the
    requests. One `httpx.AsyncClient` makes both calls, so the state cookie
    `begin` sets rides into `callback` exactly as a browser's jar would carry it.
    """
    app = _build_app(context)

    async def go() -> httpx.Response:
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        app.state.pool = pool
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                await client.get("/api/auth/begin")
                return await client.get(
                    "/api/auth/callback", params={"code": "c", "state": "s1"}
                )
        finally:
            await pool.close()

    handler = _CapturingHandler()
    auth_logger = logging.getLogger("rosadmin.web.auth")
    auth_logger.addHandler(handler)
    try:
        context.response = asyncio.run(go())
    finally:
        auth_logger.removeHandler(handler)
    context.log_records = handler.records


def _run_login_then_replay(context) -> None:
    """Log in successfully, then replay the same callback against the spent jti.

    The successful callback clears the state cookie, so the replay re-sends the
    original value to reach the jti guard - a spent assertion - rather than the
    state check, the path a real replay would take.
    """
    app = _build_app(context)

    async def go() -> tuple[httpx.Response, httpx.Response]:
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        app.state.pool = pool
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                await client.get("/api/auth/begin")
                first = await client.get(
                    "/api/auth/callback", params={"code": "c", "state": "s1"}
                )
                replay = await client.get(
                    "/api/auth/callback",
                    params={"code": "c", "state": "s1"},
                    cookies={"rosadmin_sso_state": "s1"},
                )
                return first, replay
        finally:
            await pool.close()

    context.response, context.replay = asyncio.run(go())


@when("{name} begins and returns from the login against records")
def step_login(context, name) -> None:
    _run_login(context)


@when("{name} logs in against records then replays the spent callback")
def step_login_replay(context, name) -> None:
    _run_login_then_replay(context)


@then("the login audit records the login")
def step_login_audit(context) -> None:
    discord_id = str(_DISCORD_ID)
    assert any(
        r.action == "login" and r.actor == discord_id for r in context.audit.records
    ), context.audit.records


@then("a denial audit row is recorded")
def step_denial_audit(context) -> None:
    assert any(r.action == "login_denied" for r in context.audit.records), (
        context.audit.records
    )


@then("the operator warning is logged")
def step_warning(context) -> None:
    assert any(r.levelno == logging.WARNING for r in context.log_records), (
        context.log_records
    )
