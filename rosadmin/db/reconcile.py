"""Desired state for the reconcile sweep: who belongs in which Google Group.

One REPEATABLE READ snapshot produces the whole audience map - every linked
body's leader and member groups plus the org-wide main group - so the sweep
diffs a single consistent view. `desired_for_group` re-reads one group in a
fresh short transaction; the sweep calls it immediately before applying a
group's changes, shrinking the window in which a concurrent panel write
could be undone from minutes to milliseconds.

Addresses are resolved through the same `sync_target` rule the panel's
mirror uses, and a record resolving to an unusable example-domain address is
absent from every desired set: it can never be added, and a stray copy in a
remote group diffs out as a stranger.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from rosadmin.group_sync import EXAMPLE_DOMAIN, sync_target
from rosadmin.membership.source import Email

_LINKED_LEADER_ROWS = """
    SELECT lb.leader_google_group_email AS group_email,
           m.id AS member_id, m.email, m.alternate_email
    FROM body_memberships bm
    JOIN leadership_bodies lb ON lb.id = bm.body_id
    JOIN members m ON m.id = bm.member_id
    WHERE bm.role = 'leader'
      AND lb.leader_google_group_email IS NOT NULL
      AND m.standing = 'good_standing'
"""

_LINKED_MEMBER_ROWS = """
    SELECT lb.member_google_group_email AS group_email,
           m.id AS member_id, m.email, m.alternate_email
    FROM body_memberships bm
    JOIN leadership_bodies lb ON lb.id = bm.body_id
    JOIN members m ON m.id = bm.member_id
    WHERE bm.role = 'member'
      AND lb.member_google_group_email IS NOT NULL
      AND m.standing = 'good_standing'
"""

_GOOD_STANDING_ROWS = """
    SELECT m.id AS member_id, m.email, m.alternate_email
    FROM members m
    WHERE m.standing = 'good_standing'
"""

_ONE_LINKED_GROUP_ROWS = """
    SELECT m.id AS member_id, m.email, m.alternate_email
    FROM body_memberships bm
    JOIN leadership_bodies lb ON lb.id = bm.body_id
    JOIN members m ON m.id = bm.member_id
    WHERE m.standing = 'good_standing'
      AND (
          (bm.role = 'leader' AND lb.leader_google_group_email = %(group_email)s)
          OR (bm.role = 'member' AND lb.member_google_group_email = %(group_email)s)
      )
"""


@dataclass(frozen=True)
class _AudienceRow:
    """One desired membership: a group, a member, and their two addresses."""

    group_email: str
    member_id: UUID
    email: str
    alternate_email: str | None


@dataclass(frozen=True)
class _MemberRow:
    """A good-standing member's id and addresses (main-group query rows)."""

    member_id: UUID
    email: str
    alternate_email: str | None


def _admit(
    audiences: dict[Email, dict[str, UUID]],
    group_email: Email,
    row: _AudienceRow | _MemberRow,
) -> None:
    """Resolve one row's sync address into `audiences`, or drop it.

    The example-domain drop mirrors the write path's skip gate: an address
    the gate would refuse to deliver to must not appear in desired state
    either, or every sweep would plan an add the gate then skips.
    """
    address = sync_target(row)
    if address.lower().endswith(EXAMPLE_DOMAIN):
        return
    audiences.setdefault(group_email, {})[address.casefold()] = row.member_id


async def desired_audiences(
    pool: AsyncConnectionPool, main_group_email: Email
) -> dict[Email, dict[str, UUID]]:
    """The whole audience map under one REPEATABLE READ snapshot.

    Every linked group appears as a key even when its desired set is empty -
    a group whose last good-standing member lapsed still needs its remote
    strangers removed.
    """
    async with pool.connection() as conn:
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        try:
            async with conn.transaction():
                return await _audiences_in(conn, main_group_email)
        finally:
            await conn.set_isolation_level(None)


async def _audiences_in(
    conn: psycopg.AsyncConnection, main_group_email: Email
) -> dict[Email, dict[str, UUID]]:
    audiences: dict[Email, dict[str, UUID]] = {main_group_email: {}}
    async with conn.cursor(row_factory=class_row(_AudienceRow)) as cursor:
        for statement in (_LINKED_LEADER_ROWS, _LINKED_MEMBER_ROWS):
            await cursor.execute(statement)
            for row in await cursor.fetchall():
                audiences.setdefault(Email(row.group_email), {})
                _admit(audiences, Email(row.group_email), row)
    async with conn.cursor(row_factory=class_row(_MemberRow)) as cursor:
        await cursor.execute(_GOOD_STANDING_ROWS)
        for member in await cursor.fetchall():
            _admit(audiences, main_group_email, member)
    await _include_empty_linked_groups(conn, audiences)
    return audiences


@dataclass(frozen=True)
class _LinkedGroupPair:
    """The two group emails a linked body owns; both non-null by the linked-pair check."""

    leader_google_group_email: str
    member_google_group_email: str


_ALL_LINKED_GROUPS = """
    SELECT leader_google_group_email, member_google_group_email
    FROM leadership_bodies
    WHERE leader_google_group_email IS NOT NULL
"""


async def _include_empty_linked_groups(
    conn: psycopg.AsyncConnection, audiences: dict[Email, dict[str, UUID]]
) -> None:
    async with conn.cursor(row_factory=class_row(_LinkedGroupPair)) as cursor:
        await cursor.execute(_ALL_LINKED_GROUPS)
        for pair in await cursor.fetchall():
            audiences.setdefault(Email(pair.leader_google_group_email), {})
            audiences.setdefault(Email(pair.member_google_group_email), {})


async def desired_for_group(
    pool: AsyncConnectionPool, group_email: Email, main_group_email: Email
) -> dict[str, UUID]:
    """One group's desired set, freshly read - the pre-apply recheck.

    Queries only the group in question - the main group's whole good-standing
    roster, or a linked body's leader or member rows - rather than
    recomputing the entire audience map, since the sweep calls this
    immediately before applying one group's changes and only needs that
    group's fresh view. A single SELECT is already atomically consistent, so
    no snapshot isolation is needed for a one-statement read.
    """
    if group_email == main_group_email:
        statement, params = _GOOD_STANDING_ROWS, {}
    else:
        statement, params = _ONE_LINKED_GROUP_ROWS, {"group_email": group_email}
    desired: dict[str, UUID] = {}
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=class_row(_MemberRow)) as cursor:
            await cursor.execute(statement, params)
            for row in await cursor.fetchall():
                address = sync_target(row)
                if address.lower().endswith(EXAMPLE_DOMAIN):
                    continue
                desired[address.casefold()] = row.member_id
    return desired
