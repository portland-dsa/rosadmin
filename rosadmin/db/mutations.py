"""Membership write queries: a `FOR UPDATE` claim on the body, then a
column-scoped write, both inside one transaction that commits when the
connection block exits cleanly (or rolls back on any exception).

The Google mirror call never happens in here - only after the caller's
transaction has already committed, per the concurrency discipline the write
routes follow. `member_row_by_id` and `is_leader_of` are plain reads, no
claim needed.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from rosadmin.db.directory import MemberRow

_CLAIM_BODY = "SELECT id FROM leadership_bodies WHERE id = %s FOR UPDATE"

# Race-safe by the UNIQUE constraint on (member_id, body_id), not a
# check-then-insert: a conflict means the member is already there.
_INSERT_MEMBER = """
    INSERT INTO body_memberships (member_id, body_id, role, added_by, manually_added_at)
    VALUES (%(member_id)s, %(body_id)s, 'member', %(added_by)s, now())
    ON CONFLICT (member_id, body_id) DO NOTHING
"""

# Scoped to role='member' so a leader row is unreachable even by id - removing
# a leader through this endpoint is refused, not silently downgraded.
_DELETE_MEMBER = """
    DELETE FROM body_memberships
    WHERE member_id = %(member_id)s AND body_id = %(body_id)s AND role = 'member'
"""

_MEMBER_BY_ID = """
    SELECT id, first_name, last_name, alternate_name, email, alternate_email,
           discord_user_id, standing
    FROM members
    WHERE id = %s
"""

_IS_LEADER_OF = """
    SELECT EXISTS (
        SELECT 1 FROM body_memberships
        WHERE member_id = %s AND body_id = %s AND role = 'leader'
    )
"""

#: The target's existing role in this body, read after a write matched no row -
#: distinguishing "already a member", "is a leader", and "not there at all".
#: Runs inside the claim transaction, so the body lock is still held.
_ROLE_OF = """
    SELECT role FROM body_memberships
    WHERE member_id = %(member_id)s AND body_id = %(body_id)s
"""


class ClaimOutcome(Enum):
    """What the add claim did (or refused to do) to the row."""

    Inserted = "inserted"
    AlreadyPresent = "already_present"
    #: The target already holds this body's leader row: leaders come from the
    #: membership records, and adding one as a plain member is meaningless.
    IsLeader = "is_leader"


class RemoveOutcome(Enum):
    """What the remove claim found."""

    Removed = "removed"
    NotAMember = "not_a_member"
    #: The target's row is a leader row, unreachable by this endpoint on
    #: purpose - the records pull owns leader rows, the panel never does.
    IsLeader = "is_leader"


async def _role_of(conn, *, body_id: UUID, member_id: UUID) -> str | None:
    cursor = await conn.execute(_ROLE_OF, {"member_id": member_id, "body_id": body_id})
    row = await cursor.fetchone()
    return row[0] if row is not None else None


async def claim_and_add_member(
    pool: AsyncConnectionPool, *, body_id: UUID, member_id: UUID, added_by: UUID
) -> ClaimOutcome | None:
    """Lock the body row, then insert the member row scoped to `role='member'`.

    `None` when the body itself is gone by the time this runs - defensive
    only, since a caller reaches this after `is_leader_of` already implied the
    body existed a moment earlier. `Inserted` on a fresh row; a fired unique
    constraint (`rowcount == 0`) resolves to `IsLeader` or `AlreadyPresent` by
    the existing row's role - still race-safe, since the insert goes first and
    the role read happens under the same claim.
    """
    async with pool.connection() as conn:
        cursor = await conn.execute(_CLAIM_BODY, (body_id,))
        if await cursor.fetchone() is None:
            return None
        cursor = await conn.execute(
            _INSERT_MEMBER,
            {"member_id": member_id, "body_id": body_id, "added_by": added_by},
        )
        if cursor.rowcount == 1:
            return ClaimOutcome.Inserted
        role = await _role_of(conn, body_id=body_id, member_id=member_id)
        return (
            ClaimOutcome.IsLeader if role == "leader" else ClaimOutcome.AlreadyPresent
        )


async def claim_and_remove_member(
    pool: AsyncConnectionPool, *, body_id: UUID, member_id: UUID
) -> RemoveOutcome | None:
    """Lock the body row, then delete the member row scoped to `role='member'`.

    `None` when the body itself is gone (defensive, see `claim_and_add_member`).
    A delete that matched nothing resolves to `IsLeader` when the target holds
    this body's leader row, else `NotAMember`.
    """
    async with pool.connection() as conn:
        cursor = await conn.execute(_CLAIM_BODY, (body_id,))
        if await cursor.fetchone() is None:
            return None
        cursor = await conn.execute(
            _DELETE_MEMBER, {"member_id": member_id, "body_id": body_id}
        )
        if cursor.rowcount == 1:
            return RemoveOutcome.Removed
        role = await _role_of(conn, body_id=body_id, member_id=member_id)
        return RemoveOutcome.IsLeader if role == "leader" else RemoveOutcome.NotAMember


async def member_row_by_id(
    pool: AsyncConnectionPool, member_id: UUID
) -> MemberRow | None:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(MemberRow)) as cur,
    ):
        await cur.execute(_MEMBER_BY_ID, (member_id,))
        return await cur.fetchone()


async def is_leader_of(
    pool: AsyncConnectionPool, member_id: UUID, body_id: UUID
) -> bool:
    async with pool.connection() as conn:
        cursor = await conn.execute(_IS_LEADER_OF, (member_id, body_id))
        row = await cursor.fetchone()
    assert row is not None  # EXISTS always yields exactly one row
    return row[0]
