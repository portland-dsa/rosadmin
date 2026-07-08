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

from rosadmin.membership.solidarity_tech.decode import (
    LEADERSHIP_FIELDS,
    STANDING_LABELS,
)
from rosadmin.membership.solidarity_tech.fixtures import (
    select_prop,
    status_prop,
    users_page,
)
from rosadmin.membership.source import Standing
from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import RosterEntry, is_snowflake, parse_map

DEFAULT_LIMIT = 100

#: The label the mock (and the real API) stores for an affirmative
#: `is-chapter-leader` select field.
_CHAPTER_LEADER_TRUE = "Yes"


def _discord_id_of(record: dict[str, Any] | None) -> str | None:
    """The `discord-user-id` a stored record carries, or `None` (no record,
    or a malformed one with no properties)."""
    if record is None:
        return None
    raw = record.get("custom_user_properties", {}).get("discord-user-id")
    return raw if isinstance(raw, str) else None


class _RosterStore:
    """The mutable served roster, keyed on email, holding each member's already-
    generated record dict rather than the `Persona` it came from. Regenerating a
    record from its persona enum cannot express "this persona, but lapsed" or
    "this persona, plus one more leadership body" - the field-level control
    routes patch the stored record's `custom_user_properties` in place instead.

    Assigns sequential Solidarity Tech ids in insertion order, as
    `roster.records()` does for the fixed list; a persona swap keeps its
    member's existing id, a newly-added email takes the next one.
    """

    def __init__(self, parsed: list[RosterEntry]) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        for i, entry in enumerate(parsed):
            self._records[entry.email] = entry.persona.user_json(
                i + 1, entry.email, discord_id=entry.discord_id
            )
        # From the highest live id, not len(): parse_map does not dedupe emails,
        # so a repeated email can leave the id sequence with gaps below len().
        self._next_id = (
            max((record["id"] for record in self._records.values()), default=0) + 1
        )

    def as_records(self) -> list[dict[str, Any]]:
        return list(self._records.values())

    def set(
        self, email: str, persona: Persona, discord_id: str | None = None
    ) -> dict[str, Any]:
        existing = self._records.get(email)
        st_id = existing["id"] if existing is not None else self._next_id
        if existing is None:
            self._next_id += 1
        # Without an explicit id, a swap keeps the member's Discord id: for a
        # synthetic snowflake this regenerates the same value (deterministic in
        # the kept st_id), and for an override it is what keeps a staging
        # tester's real id from being clobbered back to synthetic by a persona
        # change. An explicit id wins over both.
        if discord_id is None:
            discord_id = _discord_id_of(existing)
        record = persona.user_json(st_id, email, discord_id=discord_id)
        self._records[email] = record
        return record

    def delete(self, email: str) -> None:
        self._records.pop(email, None)

    def set_standing(self, email: str, standing: Standing) -> dict[str, Any] | None:
        """Replace the stored record's `membership-status`, or `None` if `email`
        is not on the roster."""
        record = self._records.get(email)
        if record is None:
            return None
        record["custom_user_properties"]["membership-status"] = status_prop(
            STANDING_LABELS[standing]
        )
        return record

    def set_leadership(
        self, email: str, field: str, label: str, *, present: bool
    ) -> dict[str, Any] | None:
        """Add or drop `label` under `field`, or `None` if `email` is not on the
        roster. Keeps `is-chapter-leader` in agreement with the leadership
        fields: turning a body on sets the flag if it was absent, and turning
        off the roster's last body across every leadership field clears it -
        a member deliberately marked leader with no body (`MarkedNoBody`)
        never reaches this method, so that anomaly is undisturbed.
        """
        record = self._records.get(email)
        if record is None:
            return None
        props = record["custom_user_properties"]
        entries = [e for e in props.get(field, []) if e.get("label") != label]
        if present:
            entries.extend(select_prop(label))
        if entries:
            props[field] = entries
        else:
            props.pop(field, None)
        if present:
            if not props.get("is-chapter-leader"):
                props["is-chapter-leader"] = select_prop(_CHAPTER_LEADER_TRUE)
        elif not any(props.get(f) for f in LEADERSHIP_FIELDS):
            props.pop("is-chapter-leader", None)
        return record


class _MemberUpsert(BaseModel):
    email: str
    persona: str
    #: Optional real Discord id for the record, replacing the synthetic
    #: snowflake - the live counterpart of the persona map's `persona:<id>`
    #: override. Omitted: an existing member keeps whatever id they have.
    discord_id: str | None = None


class _MemberEmail(BaseModel):
    email: str


class _StandingUpdate(BaseModel):
    email: str
    standing: str


class _LeadershipUpdate(BaseModel):
    email: str
    field: str
    label: str
    present: bool


def create_app(parsed: list[RosterEntry], *, controllable: bool = False) -> FastAPI:
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
            if body.discord_id is not None and not is_snowflake(body.discord_id):
                raise HTTPException(400, "discord_id must be numeric")
            return store.set(body.email, persona, discord_id=body.discord_id)

        @app.delete("/_control/member", status_code=204)
        def delete_member(body: _MemberEmail) -> None:
            store.delete(body.email)

        @app.post("/_control/member/standing")
        def set_standing(body: _StandingUpdate) -> dict[str, Any]:
            try:
                standing = Standing(body.standing)
            except ValueError:
                raise HTTPException(
                    400, f"unknown standing {body.standing!r}"
                ) from None
            record = store.set_standing(body.email, standing)
            if record is None:
                raise HTTPException(404, f"unknown member {body.email!r}")
            return record

        @app.post("/_control/member/leadership")
        def set_leadership(body: _LeadershipUpdate) -> dict[str, Any]:
            if body.field not in LEADERSHIP_FIELDS:
                raise HTTPException(400, f"unknown leadership field {body.field!r}")
            record = store.set_leadership(
                body.email, body.field, body.label, present=body.present
            )
            if record is None:
                raise HTTPException(404, f"unknown member {body.email!r}")
            return record

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
