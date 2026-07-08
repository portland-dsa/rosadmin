"""Read queries against the members/leadership_bodies schema, and the shared
`full_name` display rule.

Each query function opens its own cursor with a `class_row` factory, so a
result arrives as one of the frozen dataclasses below rather than a bare
tuple. Every identifier here is static; only values are bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

from rosadmin.membership.source import LeadershipAssessment, Standing


def full_name(first: str | None, last: str | None, alternate: str | None) -> str:
    """The display name: the chosen `alternate` over `first`, plus `last`."""
    return " ".join(part for part in (alternate or first, last) if part)


@dataclass(frozen=True)
class MemberRow:
    """One `members` row, as much of it as a directory read needs."""

    id: UUID
    first_name: str | None
    last_name: str | None
    alternate_name: str | None
    email: str
    alternate_email: str | None
    discord_user_id: int | None
    standing: Standing


@dataclass(frozen=True)
class BodyRow:
    """One `leadership_bodies` row."""

    id: UUID
    name: str
    body_type: str


@dataclass(frozen=True)
class LedBodyMemberRow:
    """One member of a body the acting member leads: the body, then the member."""

    body_id: UUID
    body_name: str
    body_type: str
    member_id: UUID
    first_name: str | None
    last_name: str | None
    alternate_name: str | None
    email: str
    role: str


@dataclass(frozen=True)
class GateRow:
    """The login-gate projection: a Discord id's member and leadership assessment."""

    member_id: UUID
    assessment: LeadershipAssessment


@dataclass(frozen=True)
class BodyLinkRow:
    """A body's Google linkage: both group addresses, or neither (`linked_pair`)."""

    id: UUID
    leader_google_group_email: str | None
    member_google_group_email: str | None


_MEMBER_BY_DISCORD = """
    SELECT id, first_name, last_name, alternate_name, email, alternate_email,
           discord_user_id, standing
    FROM members
    WHERE discord_user_id = %s
"""

#: Case-insensitive: stored emails are unnormalized and Solidarity Tech's own
#: `?email=` filter is case-insensitive, so a case-mismatched search must still hit.
_MEMBER_BY_EMAIL = """
    SELECT id, first_name, last_name, alternate_name, email, alternate_email,
           discord_user_id, standing
    FROM members
    WHERE lower(email) = lower(%s)
"""

#: Only bodies the member leads - a body they merely belong to never grants edit rights.
_BODIES_LED_BY = """
    SELECT lb.id, lb.name, lb.body_type
    FROM leadership_bodies lb
    JOIN body_memberships bm ON bm.body_id = lb.id
    WHERE bm.member_id = %s AND bm.role = 'leader'
    ORDER BY lb.name
"""

#: Every member of every body the acting member leads, in one round-trip: `led`
#: pins the bodies where they are a leader, `other`/`m` expand each body's roster.
_LED_BODIES_WITH_MEMBERS = """
    SELECT lb.id AS body_id, lb.name AS body_name, lb.body_type,
           m.id AS member_id, m.first_name, m.last_name, m.alternate_name,
           m.email, other.role
    FROM leadership_bodies lb
    JOIN body_memberships led
        ON led.body_id = lb.id AND led.member_id = %s AND led.role = 'leader'
    JOIN body_memberships other ON other.body_id = lb.id
    JOIN members m ON m.id = other.member_id
    ORDER BY lb.name
"""

_GATE_LOOKUP = """
    SELECT id AS member_id, leadership_assessment AS assessment
    FROM members
    WHERE discord_user_id = %s
"""

_BODY_LINK = """
    SELECT id, leader_google_group_email, member_google_group_email
    FROM leadership_bodies
    WHERE id = %s
"""


async def member_by_discord(
    pool: AsyncConnectionPool, discord_id: int
) -> MemberRow | None:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(MemberRow)) as cur,
    ):
        await cur.execute(_MEMBER_BY_DISCORD, (discord_id,))
        return await cur.fetchone()


async def member_by_email(pool: AsyncConnectionPool, email: str) -> MemberRow | None:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(MemberRow)) as cur,
    ):
        await cur.execute(_MEMBER_BY_EMAIL, (email,))
        return await cur.fetchone()


async def bodies_led_by(pool: AsyncConnectionPool, member_id: UUID) -> list[BodyRow]:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(BodyRow)) as cur,
    ):
        await cur.execute(_BODIES_LED_BY, (member_id,))
        return await cur.fetchall()


async def led_bodies_with_members(
    pool: AsyncConnectionPool, member_id: UUID
) -> list[LedBodyMemberRow]:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(LedBodyMemberRow)) as cur,
    ):
        await cur.execute(_LED_BODIES_WITH_MEMBERS, (member_id,))
        return await cur.fetchall()


async def gate_lookup(pool: AsyncConnectionPool, discord_id: int) -> GateRow | None:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(GateRow)) as cur,
    ):
        await cur.execute(_GATE_LOOKUP, (discord_id,))
        return await cur.fetchone()


async def body_link(pool: AsyncConnectionPool, body_id: UUID) -> BodyLinkRow | None:
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=class_row(BodyLinkRow)) as cur,
    ):
        await cur.execute(_BODY_LINK, (body_id,))
        return await cur.fetchone()
