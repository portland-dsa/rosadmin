"""The FastAPI app: a served roster over `GET /users`, with an optional control
surface for mutating it at runtime.

Consumed three ways: read-only test suites reach it in-process over
`httpx.ASGITransport`, injecting a fixed persona map; the staging service reaches
it as a bound port under uvicorn, pointed at by the staging configuration's
backend base URL; and an operator drives its `/_control` routes with curl to
cycle a persona without restarting the process.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from rosadmin.membership.solidarity_tech.fixtures import users_page
from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import parse_map

DEFAULT_LIMIT = 100


class _RosterStore:
    """The mutable served roster, keyed on email. Assigns sequential Solidarity
    Tech ids in insertion order, as `roster.records()` does for the fixed list;
    a persona swap keeps its member's existing id, a newly-added email takes the
    next one."""

    def __init__(self, parsed: list[tuple[str, Persona]]) -> None:
        self._entries: dict[str, tuple[int, Persona]] = {
            email: (i + 1, persona) for i, (email, persona) in enumerate(parsed)
        }
        # From the highest live id, not len(): the comprehension above dedupes emails
        # while parse_map does not, so len() can sit below the max id and collide.
        self._next_id = max((sid for sid, _ in self._entries.values()), default=0) + 1

    def as_records(self) -> list[dict[str, Any]]:
        return [
            persona.user_json(st_id, email)
            for email, (st_id, persona) in self._entries.items()
        ]

    def set(self, email: str, persona: Persona) -> dict[str, Any]:
        existing = self._entries.get(email)
        st_id = existing[0] if existing is not None else self._next_id
        if existing is None:
            self._next_id += 1
        self._entries[email] = (st_id, persona)
        return persona.user_json(st_id, email)

    def delete(self, email: str) -> None:
        self._entries.pop(email, None)


class _MemberUpsert(BaseModel):
    email: str
    persona: str


class _MemberEmail(BaseModel):
    email: str


def create_app(
    parsed: list[tuple[str, Persona]], *, controllable: bool = False
) -> FastAPI:
    """Build the mock over a parsed persona map.

    `controllable=True` also mounts the `/_control` mutation routes; the suites
    that inject a fixed read-only mock keep the default, so they never gain a
    mutation surface.
    """
    app = FastAPI()
    store = _RosterStore(parsed)

    @app.get("/users")
    def list_users(
        limit: int = Query(DEFAULT_LIMIT, alias="_limit"),
        offset: int = Query(0, alias="_offset"),
        email: str | None = Query(None),
    ) -> dict[str, Any]:
        roster = store.as_records()
        rows = _filter_by_email(roster, email) if email is not None else roster
        page = rows[offset : offset + limit]
        return users_page(page, len(rows), limit, offset)

    if controllable:

        @app.put("/_control/member")
        def set_member(body: _MemberUpsert) -> dict[str, Any]:
            persona = Persona.parse(body.persona)
            if persona is None:
                raise HTTPException(400, f"unknown persona {body.persona!r}")
            return store.set(body.email, persona)

        @app.delete("/_control/member", status_code=204)
        def delete_member(body: _MemberEmail) -> None:
            store.delete(body.email)

    return app


def _filter_by_email(rows: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """Rows whose top-level email equals `wanted`, case-insensitively.

    The mock's stand-in for the live `?email=` server-side filter. A null-email
    record (the malformed persona) never matches.
    """
    target = wanted.casefold()
    return [
        r
        for r in rows
        if isinstance(r.get("email"), str) and r["email"].casefold() == target
    ]


def app_from_env() -> FastAPI:
    """Build the controllable app from `SOLIDARITY_TECH_MOCK_PERSONAS` (empty
    string -> empty roster)."""
    return create_app(
        parse_map(os.environ.get("SOLIDARITY_TECH_MOCK_PERSONAS", "")),
        controllable=True,
    )


def main() -> None:
    """Serve the env-configured mock as a bound port (the staging entry point)."""
    import uvicorn

    uvicorn.run(
        app_from_env(),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8001")),
    )


if __name__ == "__main__":
    main()
