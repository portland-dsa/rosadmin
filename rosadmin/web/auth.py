"""Session plumbing: the cookie, the dependency every route hangs on, and
the auth endpoints.

Identity comes only from the session cookie. The Origin middleware is a visible guard
on top of SameSite: a state-changing request from a foreign origin is refused even
if a cookie somehow rode along. `begin`/`callback` are reserved paths
for the real OAuth relay; they answer 501 with a stable code so the client can wire real URLs today.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from rosadmin.db.audit import record_best_effort
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.sessions import LeaderContext, SessionStore

SESSION_COOKIE = "rosadmin_session"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _store(request: Request) -> SessionStore:
    return request.app.state.session_store


async def require_leader(request: Request) -> LeaderContext:
    """Resolve the session cookie or refuse; handlers receive a real context."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        raise AppProblem(401, ProblemCode.NOT_AUTHENTICATED, "no session")
    leader = await _store(request).resolve(token)
    if leader is None:
        raise AppProblem(
            401, ProblemCode.NOT_AUTHENTICATED, "session expired or unknown"
        )
    return leader


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
                "code": str(ProblemCode.FORBIDDEN_ORIGIN),
            },
            status_code=403,
            media_type="application/problem+json",
        )
    return await call_next(request)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=True, samesite="lax"
    )


auth_router = APIRouter(prefix="/api/auth")


@auth_router.get("/begin", status_code=501)
async def begin() -> None:
    raise AppProblem(
        501, ProblemCode.AUTH_NOT_IMPLEMENTED, "OAuth login not yet available"
    )


@auth_router.get("/callback", status_code=501)
async def callback() -> None:
    raise AppProblem(
        501, ProblemCode.AUTH_NOT_IMPLEMENTED, "OAuth login not yet available"
    )


@auth_router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        leader = await _store(request).resolve(token)
        await _store(request).revoke(token)
        if leader is not None:
            await record_best_effort(
                request.app.state.audit_sink, "logout", actor=str(leader.member_id)
            )
    response.delete_cookie(SESSION_COOKIE)
