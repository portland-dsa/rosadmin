"""The records-backed `MemberDirectory` and `GroupModify`: reads and
mutations over the real schema.

Maps the same rows into the Pydantic contract the routes return. The record
*shapes* match `StubDirectory`'s, but the data differs for now: the pull
writes only `role='leader'` rows, so a records-backed group's member list
currently holds just that body's leaders, where the stub also seeds members.

`RecordsGroupModify` claims each write in its own transaction (`db.mutations`)
and calls the Google mirror only after that transaction has committed - never
inside it. The mirror call and its audit row run in a background task spawned
right after the commit, so the HTTP response returns at DB-commit time rather
than paying for a Google round trip the caller never sees the result of. A
`Failed` mirror outcome changes nothing about either the response or the
write: the write already stands, and the audit row plus the sync's own error
log are the record of what happened.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from rosadmin.db.audit import AuditSink, record_best_effort
from rosadmin.db.directory import (
    BodyLinkRow,
    MemberRow,
    bodies_led_by,
    body_link,
    full_name,
    led_bodies_with_members,
    member_by_discord,
    member_by_email,
)
from rosadmin.db.mutations import (
    ClaimOutcome,
    RemoveOutcome,
    claim_and_add_member,
    claim_and_remove_member,
    is_leader_of,
    member_row_by_id,
)
from rosadmin.group_sync import GroupSync, sync_target
from rosadmin.membership.source import Email, Standing
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

logger = logging.getLogger(__name__)


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


async def _resolve_member(pool: AsyncConnectionPool, principal: Principal) -> MemberRow:
    """The acting member behind a session, or 404 - shared by reads and mutations."""
    row = await member_by_discord(pool, int(principal.discord_id))
    if row is None:
        raise AppProblem(404, ProblemCode.NotFound, "unknown principal")
    return row


def _group_email(link: BodyLinkRow) -> Email | None:
    return (
        Email(link.member_google_group_email)
        if link.member_google_group_email
        else None
    )


class RecordsDirectory:
    """The `MemberDirectory` over the `members`/`leadership_bodies` schema."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def _member(self, principal: Principal) -> MemberRow:
        return await _resolve_member(self._pool, principal)

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


class RecordsGroupModify:
    """The `GroupModify` over the same schema: leader-scoped add/remove."""

    def __init__(
        self, pool: AsyncConnectionPool, group_sync: GroupSync, audit_sink: AuditSink
    ) -> None:
        self._pool = pool
        self._group_sync = group_sync
        self._audit_sink = audit_sink
        # Strong references to in-flight mirror tasks: asyncio only holds a
        # weak reference to a task, so without this a task with no other
        # referent can be garbage-collected mid-flight.
        self._mirror_tasks: set[asyncio.Task[None]] = set()

    async def _require_leader(self, principal: Principal, group_id: UUID) -> MemberRow:
        actor = await _resolve_member(self._pool, principal)
        # A non-leader and an unknown body answer identically: existence of a
        # body this principal does not lead is not disclosed to them.
        if not await is_leader_of(self._pool, actor.id, group_id):
            raise AppProblem(404, ProblemCode.NotFound, "unknown group")
        return actor

    def _spawn_mirror(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(self._run_mirror(coro))
        self._mirror_tasks.add(task)
        task.add_done_callback(self._mirror_tasks.discard)

    @staticmethod
    async def _run_mirror(coro: Coroutine[Any, Any, None]) -> None:
        # The last-resort net: `GroupSync` already maps every expected Google
        # failure to a `SyncOutcome` instead of raising, so anything reaching
        # here is unexpected - a background task's exception otherwise vanishes
        # silently instead of surfacing to a caller. Never logs a member
        # address; only what the coroutine itself would have logged does.
        try:
            await coro
        except Exception:
            logger.error("group mirror task failed unexpectedly", exc_info=True)

    async def drain(self) -> None:
        """Await every mirror task in flight right now.

        Tests call this after an HTTP round trip to observe the mirror's
        outcome before asserting on it; the shutdown path calls it before the
        pool closes, so a mirror task is never left running against a closed
        pool.
        """
        pending = list(self._mirror_tasks)
        await asyncio.gather(*pending, return_exceptions=True)

    async def _mirror_add(
        self,
        actor_id: UUID,
        group_id: UUID,
        member_id: UUID,
        target: MemberRow,
        group_email: Email | None,
    ) -> None:
        outcome = await self._group_sync.add(group_email, sync_target(target))
        await record_best_effort(
            self._audit_sink,
            "group.member_added",
            actor=str(actor_id),
            subject=str(member_id),
            detail={"body_id": str(group_id), "google": outcome.value},
        )

    async def _mirror_remove(
        self,
        actor_id: UUID,
        group_id: UUID,
        member_id: UUID,
        target: MemberRow,
        group_email: Email | None,
    ) -> None:
        outcome = await self._group_sync.remove(group_email, sync_target(target))
        await record_best_effort(
            self._audit_sink,
            "group.member_removed",
            actor=str(actor_id),
            subject=str(member_id),
            detail={"body_id": str(group_id), "google": outcome.value},
        )

    async def _member_group_email(self, group_id: UUID) -> Email | None:
        """The body's member-group address, resolved before the mirror spawns.

        Captured by value so the task depends on nothing mutable; a body with
        no linkage row (or none linked) yields None, which the sync's unlinked
        gate turns into a recorded skip rather than a crash.
        """
        link = await body_link(self._pool, group_id)
        return _group_email(link) if link is not None else None

    async def add_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> GroupMember:
        actor = await self._require_leader(principal, group_id)

        target = await member_row_by_id(self._pool, member_id)
        if target is None:
            raise AppProblem(404, ProblemCode.MemberNotFound, "no such member")
        if target.standing is not Standing.GoodStanding:
            raise AppProblem(
                409, ProblemCode.MemberNotEligible, "member is not in good standing"
            )

        claimed = await claim_and_add_member(
            self._pool, body_id=group_id, member_id=member_id, added_by=actor.id
        )
        if claimed is None:
            raise AppProblem(404, ProblemCode.NotFound, "unknown group")
        if claimed is ClaimOutcome.IsLeader:
            raise AppProblem(
                409, ProblemCode.AlreadyLeader, "already a leader of this group"
            )
        if claimed is ClaimOutcome.AlreadyPresent:
            raise AppProblem(409, ProblemCode.AlreadyMember, "already a member")

        group_email = await self._member_group_email(group_id)
        self._spawn_mirror(
            self._mirror_add(actor.id, group_id, member_id, target, group_email)
        )
        return GroupMember(
            id=target.id,
            full_name=_display_name(target),
            email=target.email,
            role=Role.Member,
        )

    async def remove_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> None:
        actor = await self._require_leader(principal, group_id)

        # Read the target before the claim, mirroring add_member: the mirror
        # needs the row's addresses, and reading after the delete commits
        # would race a concurrent member-record deletion. A missing record
        # answers exactly like a missing membership row - it has neither.
        target = await member_row_by_id(self._pool, member_id)
        if target is None:
            raise AppProblem(404, ProblemCode.NotAMember, "not a member")

        removed = await claim_and_remove_member(
            self._pool, body_id=group_id, member_id=member_id
        )
        if removed is None or removed is RemoveOutcome.NotAMember:
            raise AppProblem(404, ProblemCode.NotAMember, "not a member")
        if removed is RemoveOutcome.IsLeader:
            raise AppProblem(
                403,
                ProblemCode.LeaderNotRemovable,
                "leaders are managed by the membership records, not the panel",
            )

        group_email = await self._member_group_email(group_id)
        self._spawn_mirror(
            self._mirror_remove(actor.id, group_id, member_id, target, group_email)
        )
