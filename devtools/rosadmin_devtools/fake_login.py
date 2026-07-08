"""The development login door: impersonate a persona, mint a real session.

Only the identity-verification half is fake - the session mint and the cookie are
the same machinery the real login will use. The route reaches production never:
this module ships only where rosadmin-devtools is installed, and the app registers
it only when the fake-login setting is also on.

Two arrangements serve it. With the stub directory wired (the injected-store test
rig), the stub's own persona map mints the principal. With records-backed reads
(local development against a real database), the persona resolves through the
same `SOLIDARITY_TECH_MOCK_PERSONAS` map the mock source serves: persona name ->
the map's first matching email -> the pulled member row -> its numeric Discord
id. That keeps one source of truth for who a persona is, and it means the minted
principal is exactly the one the records directory can resolve - but only after
a roster pull has materialized the row, which the error below spells out.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request, Response
from psycopg_pool import AsyncConnectionPool

from rosadmin.db.audit import record_best_effort
from rosadmin.db.directory import gate_lookup, member_by_email
from rosadmin.membership.source import LeadershipAssessment
from rosadmin.mock_st.roster import parse_map
from rosadmin.web.auth import set_session_cookie
from rosadmin.web.models import FakeLoginRequest, MeResponse
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.sessions import DiscordUserId, Principal, SessionStore
from rosadmin_devtools.stubs import StubDirectory

fake_login_router = APIRouter(prefix="/api/auth")


async def _records_principal(pool: AsyncConnectionPool, persona_name: str) -> Principal:
    """Resolve a persona against the pulled records, mirroring the real gate.

    The chapter-leader check reuses the stored `leadership_assessment`, so a
    fake login admits exactly the personas the production gate would - the only
    thing skipped is Discord itself.
    """
    persona_map = parse_map(os.environ.get("SOLIDARITY_TECH_MOCK_PERSONAS", ""))
    email = next(
        (entry.email for entry in persona_map if entry.persona.value == persona_name),
        None,
    )
    if email is None:
        raise AppProblem(
            404,
            ProblemCode.UnknownPersona,
            "no such persona in SOLIDARITY_TECH_MOCK_PERSONAS",
        )
    row = await member_by_email(pool, email)
    if row is None or row.discord_user_id is None:
        raise AppProblem(
            404,
            ProblemCode.NotFound,
            "persona has no pulled member row - run `rosadmin roster pull` first",
        )
    gate = await gate_lookup(pool, row.discord_user_id)
    if gate is None or gate.assessment is not LeadershipAssessment.Leader:
        raise AppProblem(
            403, ProblemCode.NotChapterLeader, "persona is not a chapter leader"
        )
    return Principal(discord_id=DiscordUserId(str(row.discord_user_id)))


@fake_login_router.post("/fake-login", response_model=MeResponse)
async def fake_login(
    request: Request, response: Response, body: FakeLoginRequest
) -> MeResponse:
    directory = request.app.state.directory
    store: SessionStore = request.app.state.session_store
    if isinstance(directory, StubDirectory):
        principal = directory.principal_for(body.persona)
    else:
        principal = await _records_principal(request.app.state.pool, body.persona)
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
