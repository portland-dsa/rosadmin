"""The records-backed `MemberDirectory`: reads over the real schema.

Maps the same rows into the Pydantic contract the routes return. The record
*shapes* match `StubDirectory`'s, but the data differs for now: the pull
writes only `role='leader'` rows, so a records-backed group's member list
currently holds just that body's leaders, where the stub also seeds members.
Mutations are not implemented here; a build wiring `RecordsDirectory` leaves
`app.state.group_modify` unset, so those routes answer 501.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from rosadmin.db.directory import (
    MemberRow,
    bodies_led_by,
    full_name,
    led_bodies_with_members,
    member_by_discord,
    member_by_email,
)
from rosadmin.membership.source import Standing
from rosadmin.web.models import (
    Group,
    GroupMember,
    GroupSummary,
    Member,
    Role,
    SearchHit,
    SearchMiss,
)
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.sessions import Principal


class _NameParts(Protocol):
    """A row carrying the three name columns `full_name` composes.

    Read-only properties, not bare attributes, so the frozen row dataclasses
    satisfy it structurally (a writable protocol attribute would not match)."""

    @property
    def first_name(self) -> str | None: ...
    @property
    def last_name(self) -> str | None: ...
    @property
    def alternate_name(self) -> str | None: ...


def _display_name(row: _NameParts) -> str:
    return full_name(row.first_name, row.last_name, row.alternate_name)


class RecordsDirectory:
    """The `MemberDirectory` over the `members`/`leadership_bodies` schema."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def _member(self, principal: Principal) -> MemberRow:
        row = await member_by_discord(self._pool, int(principal.discord_id))
        if row is None:
            raise AppProblem(404, ProblemCode.NotFound, "unknown principal")
        return row

    async def search(self, email: str) -> SearchHit | SearchMiss:
        row = await member_by_email(self._pool, email)
        if row is None:
            return SearchMiss(status="not_found")
        if row.standing is Standing.Lapsed:
            return SearchMiss(status="dues_expired")
        return SearchHit(
            status="good_standing",
            member=Member(id=row.id, full_name=_display_name(row), email=row.email),
        )

    async def display_name_for(self, principal: Principal) -> str:
        return _display_name(await self._member(principal))

    async def summaries_for(self, principal: Principal) -> list[GroupSummary]:
        member = await self._member(principal)
        bodies = await bodies_led_by(self._pool, member.id)
        return [
            GroupSummary(id=body.id, name=body.name, body_type=body.body_type)
            for body in bodies
        ]

    async def groups_for(self, principal: Principal) -> list[Group]:
        member = await self._member(principal)
        # One round-trip returns every led body's full membership; group the flat
        # rows by body, preserving the query's name ordering (dict insertion order).
        bodies: dict[UUID, tuple[str, str, list[GroupMember]]] = {}
        for row in await led_bodies_with_members(self._pool, member.id):
            _name, _type, members = bodies.setdefault(
                row.body_id, (row.body_name, row.body_type, [])
            )
            members.append(
                GroupMember(
                    id=row.member_id,
                    full_name=_display_name(row),
                    email=row.email,
                    role=Role(row.role),
                )
            )
        return [
            Group(
                id=body_id,
                name=name,
                body_type=body_type,
                members=sorted(members, key=lambda m: m.full_name),
            )
            for body_id, (name, body_type, members) in bodies.items()
        ]
