"""The FastAPI app: one read-only `GET /users` route over the served roster.

Consumed two ways: tests reach it in-process over `httpx.ASGITransport`; the
staging service reaches it as a bound port under uvicorn, pointed at by the
staging configuration's backend base URL.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Query

from rosadmin.membership.solidarity_tech.fixtures import users_page
from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import parse_map, records

DEFAULT_LIMIT = 100


def create_app(parsed: list[tuple[str, Persona]]) -> FastAPI:
    """Build the read-only mock over a parsed persona map."""
    app = FastAPI()
    roster = records(parsed)

    @app.get("/users")
    def list_users(
        limit: int = Query(DEFAULT_LIMIT, alias="_limit"),
        offset: int = Query(0, alias="_offset"),
        email: str | None = Query(None),
    ) -> dict[str, Any]:
        rows = _filter_by_email(roster, email) if email is not None else roster
        page = rows[offset : offset + limit]
        return users_page(page, len(rows), limit, offset)

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
    """Build the app from `SOLIDARITY_TECH_MOCK_PERSONAS` (empty string -> empty roster)."""
    return create_app(parse_map(os.environ.get("SOLIDARITY_TECH_MOCK_PERSONAS", "")))


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
