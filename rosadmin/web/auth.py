"""Session plumbing: the cookie, the dependency every route hangs on, and
the auth endpoints.

Identity comes only from the session cookie. The Origin middleware is a visible guard
on top of SameSite: a state-changing request from a foreign origin is refused even
if a cookie somehow rode along. `begin`/`callback` relay the login to botonio's SSO
socket: `begin` starts it and hands the browser botonio's authorize URL, `callback`
redeems the Discord round-trip, verifies the signed assertion, and - only for a
verified member the records gate marks a chapter leader - mints a session. Every
outcome, good or bad, ends in a redirect back to the app and is audited.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from rosadmin.db.audit import record_best_effort
from rosadmin.db.directory import gate_lookup
from rosadmin.membership.source import ANOMALY_WARNING, LeadershipAssessment
from rosadmin.sso import (
    AssertionRejected,
    SsoConfig,
    SsoUnreachable,
    Standing,
    VerifiedAssertion,
    sso_begin,
    sso_complete,
    verify_assertion,
)
from rosadmin.web.clock import utcnow
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.rate_limit import rate_limited
from rosadmin.web.sessions import Principal, SessionStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "rosadmin_session"
SSO_STATE_COOKIE = "rosadmin_sso_state"
_STATE_MAX_AGE = 300  # seconds; the login front-channel is short-lived.
_ANONYMOUS = "anonymous"  # the audit actor before an assertion is verified.

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _store(request: Request) -> SessionStore:
    return request.app.state.session_store


async def require_session(request: Request) -> Principal:
    """Resolve the session cookie or refuse; handlers receive a real principal."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise AppProblem(401, ProblemCode.NotAuthenticated, "no session")
    principal = await _store(request).resolve(token)
    if principal is None:
        raise AppProblem(
            401, ProblemCode.NotAuthenticated, "session expired or unknown"
        )
    return principal


async def origin_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Refuse cross-origin state changes when an expected origin is configured."""
    allowed: str | None = request.app.state.settings.allowed_origin
    origin = request.headers.get("origin")
    if (
        allowed is not None
        and origin is not None
        and origin != allowed
        and request.method in _MUTATING_METHODS
    ):
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "cross-origin request refused",
                "status": 403,
                "code": str(ProblemCode.ForbiddenOrigin),
            },
            status_code=403,
            media_type="application/problem+json",
        )
    return await call_next(request)


def _set_auth_cookie(
    response: Response, name: str, value: str, *, max_age: int | None = None
) -> None:
    """Set an auth cookie with the attributes every one of ours shares.

    Setting and clearing go through one place so a cookie and its later deletion
    cannot drift apart: a browser only treats a deletion as matching the cookie
    it holds when the path and security attributes line up (see
    [`_clear_auth_cookie`])."""
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )


def _clear_auth_cookie(response: Response, name: str) -> None:
    """Delete an auth cookie, matching the attributes it was set with.

    A `delete_cookie` that drops `Secure`/`HttpOnly` does not line up with the
    cookie the browser is holding, so instead of removing it the browser keeps an
    empty-valued cookie and goes on sending `name=""` - which then fails the
    equality check the state cookie exists to guard. Matching the attributes is
    what makes the deletion actually delete."""
    response.delete_cookie(name, path="/", httponly=True, secure=True, samesite="lax")


def set_session_cookie(response: Response, token: str) -> None:
    _set_auth_cookie(response, SESSION_COOKIE, token)


def _set_state_cookie(response: Response, state: str) -> None:
    _set_auth_cookie(response, SSO_STATE_COOKIE, state, max_age=_STATE_MAX_AGE)


def _app_redirect(query: str) -> RedirectResponse:
    """Send the browser back to the app. Same-origin, so a path is enough."""
    return RedirectResponse(url=f"/{query}", status_code=302)


def _fail(query: str) -> RedirectResponse:
    """Every callback failure exit shares this: the same-origin redirect, and
    the state cookie cleared so no stale state lingers past this attempt."""
    response = _app_redirect(query)
    _clear_auth_cookie(response, SSO_STATE_COOKIE)
    return response


async def _audit_failed(request: Request, stage: str) -> None:
    """Audit a pre-verification exit: no assertion exists yet, so no real actor."""
    await record_best_effort(
        request.app.state.audit_sink,
        "login_failed",
        actor=_ANONYMOUS,
        detail={"stage": stage},
    )


async def _audit_denied(
    request: Request, assertion: VerifiedAssertion, stage: str
) -> None:
    """Audit a post-verification exit: the assertion is real, but the login is refused."""
    await record_best_effort(
        request.app.state.audit_sink,
        "login_denied",
        actor=assertion.discord_id,
        detail={"stage": stage},
    )


auth_router = APIRouter(prefix="/api/auth")


@auth_router.get("/begin", dependencies=[Depends(rate_limited)])
async def begin(request: Request) -> Response:
    config: SsoConfig = request.app.state.sso
    try:
        begun = await sso_begin(config.settings, config.bearer)
    except SsoUnreachable:
        return _app_redirect("?login=failed")
    response = RedirectResponse(url=begun.authorize_url, status_code=302)
    _set_state_cookie(response, begun.state)
    return response


@auth_router.get("/callback", dependencies=[Depends(rate_limited)])
async def callback(
    request: Request, code: str | None = None, state: str | None = None
) -> Response:
    config: SsoConfig = request.app.state.sso
    cookie_state = request.cookies.get(SSO_STATE_COOKIE)
    if cookie_state is None or state is None or state == "" or cookie_state != state:
        await _audit_failed(request, "rejected")
        return _fail("?login=failed")
    if code is None:
        await _audit_failed(request, "rejected")
        return _fail("?login=failed")

    try:
        token = await sso_complete(config.settings, config.bearer, code, state)
        assertion = verify_assertion(token, config.keys, config.settings, now=utcnow())
    except SsoUnreachable:
        await _audit_failed(request, "unreachable")
        return _fail("?login=failed")
    except AssertionRejected:
        await _audit_failed(request, "rejected")
        return _fail("?login=failed")

    # Burn the jti before the grant check, so even a replayed non-member cannot retry.
    fresh = await request.app.state.jti_cache.claim(assertion.jti, assertion.exp)
    if not fresh:
        await _audit_denied(request, assertion, "replay")
        return _fail("?login=failed")

    # The guild match is already enforced upstream in verify_assertion (a mismatch
    # raises WrongGuild there); it is restated here so the full grant rule - member
    # standing AND home guild - reads at the one decision point. The redirect carries
    # no reason: botonio denials and the records gate below share one uninformative
    # exit, so a probing caller cannot tell why they were refused.
    if (
        assertion.standing is not Standing.Member
        or assertion.guild != config.settings.guild_id
    ):
        await _audit_denied(request, assertion, "standing")
        return _fail("?login=denied")

    # The grant rule's second half: botonio only vouches for Discord membership,
    # not chapter leadership, so the stored assessment is the one place that
    # decision is made. Absence from the records cache and "not currently a
    # leader" both deny the same way; only the anomalous assessments - the raw
    # flag and the derived roles disagreeing - are worth an operator's attention.
    try:
        discord_id = int(assertion.discord_id)
    except ValueError:
        # botonio verified the signature, but the sub is not a numeric Discord id -
        # nothing to look up. Deny uniformly rather than 500 on the conversion, so a
        # malformed id cannot be told apart from any other refusal.
        await _audit_denied(request, assertion, "malformed_id")
        return _fail("?login=denied")
    row = await gate_lookup(request.app.state.pool, discord_id)
    if row is None:
        await _audit_denied(request, assertion, "not_in_cache")
        return _fail("?login=denied")
    if row.assessment.is_anomalous:
        logger.warning(ANOMALY_WARNING, row.member_id, row.assessment.value)
    if row.assessment is not LeadershipAssessment.Leader:
        await _audit_denied(request, assertion, "not_chapter_leader")
        return _fail("?login=denied")

    session_token = await _store(request).create(
        Principal(discord_id=assertion.discord_id)
    )
    response = _app_redirect("")
    set_session_cookie(response, session_token)
    _clear_auth_cookie(response, SSO_STATE_COOKIE)
    await record_best_effort(
        request.app.state.audit_sink,
        "login",
        actor=assertion.discord_id,
        detail={"method": "sso"},
    )
    return response


@auth_router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        principal = await _store(request).resolve(token)
        await _store(request).revoke(token)
        if principal is not None:
            await record_best_effort(
                request.app.state.audit_sink, "logout", actor=principal.discord_id
            )
    _clear_auth_cookie(response, SESSION_COOKIE)
