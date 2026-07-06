# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import httpx
import respx
from behave import given, then, when
from fastapi.testclient import TestClient

from rosadmin.db.audit import RecordingAuditSink
from rosadmin.service import create_app
from rosadmin.sso import SigningKeys, SsoConfig, SsoSettings
from rosadmin.web.jti import InMemoryJtiCache
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings
from tests.support.paseto import keypair, sign_assertion

_SETTINGS = SsoSettings(
    iss="botonio",
    aud="rosadmin",
    kid="v1",
    guild_id="42",
    socket_path="/tmp/botonio.sock",
)


def _assertion(signing_key, *, standing="member", kid="v1", malformed=False):
    if malformed:
        return "v4.public.not-a-real-token"
    return sign_assertion(
        signing_key, standing=standing, kid=kid, sub="777", jti="jti-777"
    )


def _build(context, assertion):
    signing_key, raw_pub = keypair()
    context.audit = RecordingAuditSink()
    context.app = create_app(
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
        return_value=httpx.Response(200, json={"assertion": assertion(signing_key)})
    )


@given("botonio will complete with a member assertion for Ralsei")
def step_member(context):
    _build(context, lambda k: _assertion(k, standing="member"))


@given("botonio will complete with a dues_expired assertion for Ralsei")
def step_lapsed(context):
    _build(context, lambda k: _assertion(k, standing="dues_expired"))


@given("botonio will complete with a malformed assertion")
def step_malformed(context):
    _build(context, lambda k: _assertion(k, malformed=True))


@given("botonio will complete with an assertion signed under an unpinned key")
def step_unpinned_kid(context):
    # The app pins only "v1"; a token whose kid claim is "v2" names a key rosadmin
    # does not hold, so verify_assertion raises UnknownKeyId - a member of the
    # AssertionRejected tree the callback already catches.
    _build(context, lambda k: _assertion(k, kid="v2"))


@when("Ralsei begins and returns from the login")
def step_login(context):
    context.client = TestClient(context.app, follow_redirects=False)
    begin = context.client.get("/api/auth/begin")
    context.state = begin.cookies.get("rosadmin_sso_state")
    context.response = context.client.get(
        "/api/auth/callback",
        params={"code": "c", "state": "s1"},
        cookies={"rosadmin_sso_state": context.state or "s1"},
    )


@when("the same callback is replayed")
def step_replay(context):
    context.replay = context.client.get(
        "/api/auth/callback",
        params={"code": "c", "state": "s1"},
        cookies={"rosadmin_sso_state": context.state or "s1"},
    )


@then("a session cookie is set")
def step_cookie(context):
    assert context.response.cookies.get("rosadmin_session"), context.response.headers


@then("the login audit records the Discord id")
def step_audit(context):
    assert any(r.action == "login" and r.actor == "777" for r in context.audit.records)


@then("the replay is refused with no new session")
def step_refused(context):
    assert context.replay.cookies.get("rosadmin_session") is None


@then("the login fails with no session")
def step_failed(context):
    assert context.response.cookies.get("rosadmin_session") is None
    assert "login=failed" in context.response.headers.get("location", "")


@then('the login is denied with reason "{reason}"')
def step_denied(context, reason):
    assert context.response.cookies.get("rosadmin_session") is None
    assert f"login=denied&reason={reason}" in context.response.headers.get(
        "location", ""
    )
