"""The development login door: impersonate a persona, mint a real session.

Only the identity-verification half is fake - the session mint and the cookie are
the same machinery the real login will use. The route reaches production never:
this module ships only where rosadmin-devtools is installed, and the app registers
it only when the fake-login setting is also on.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from rosadmin.db.audit import record_best_effort
from rosadmin.web.auth import set_session_cookie
from rosadmin.web.models import FakeLoginRequest, MeResponse
from rosadmin.web.sessions import SessionStore
from rosadmin_devtools.stubs import StubDirectory

fake_login_router = APIRouter(prefix="/api/auth")


@fake_login_router.post("/fake-login", response_model=MeResponse)
async def fake_login(
    request: Request, response: Response, body: FakeLoginRequest
) -> MeResponse:
    directory: StubDirectory = request.app.state.directory
    store: SessionStore = request.app.state.session_store
    principal = directory.principal_for(body.persona)
    token = await store.create(principal)
    await record_best_effort(
        request.app.state.audit_sink,
        "login",
        actor=principal.discord_id,
        detail={"method": "fake"},
    )
    set_session_cookie(response, token)
    return MeResponse(
        display_name=await directory.display_name_for(principal),
        groups=await directory.summaries_for(principal),
    )
