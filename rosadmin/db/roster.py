"""The roster pull: upsert a Solidarity Tech roster into the local members and
leadership tables, and reconcile the leader rows to match.

The whole pull is one `REPEATABLE READ` transaction, so an unexpected mid-pull
failure rolls it all back and a retry is always safe. Each member is upserted
under its own savepoint, though, so a single record clashing with another's
unique email or Discord id is skipped and reported, not fatal. Solidarity Tech
is read-only to this codebase, so the only store a pull can affect is this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from rosadmin.membership.source import Leadership, LeadershipAssessment, Member, assess

_UPSERT_MEMBER = """
    INSERT INTO members
        (st_id, first_name, last_name, email, discord_user_id, standing,
         is_chapter_leader, leadership_assessment)
    VALUES
        (%(st_id)s, %(first_name)s, %(last_name)s, %(email)s, %(discord_id)s,
         %(standing)s, %(is_chapter_leader)s, %(assessment)s)
    ON CONFLICT (st_id) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        email = EXCLUDED.email,
        discord_user_id = EXCLUDED.discord_user_id,
        standing = EXCLUDED.standing,
        is_chapter_leader = EXCLUDED.is_chapter_leader,
        leadership_assessment = EXCLUDED.leadership_assessment
    RETURNING id
"""

#: `ON CONFLICT ... DO UPDATE` rather than `DO NOTHING` so `RETURNING` yields
#: the row's id whether it was just inserted or already present - the update
#: itself is a no-op.
_UPSERT_BODY = """
    INSERT INTO leadership_bodies (name, body_type)
    VALUES (%(name)s, %(body_type)s)
    ON CONFLICT (name, body_type) DO UPDATE SET name = EXCLUDED.name
    RETURNING id
"""

#: The global leader reconcile: driven off parallel arrays through `unnest`
#: rather than a literal `IN (...)`/`NOT IN (...)`, which is invalid SQL when
#: the roster has no leaders at all. `unnest` on two empty arrays yields no
#: rows, so an empty roster reconciles correctly to "no leader rows".
_INSERT_LEADER_ROWS = """
    INSERT INTO body_memberships (member_id, body_id, role)
    SELECT d.member_id, d.body_id, 'leader'
    FROM unnest(%(member_ids)s::uuid[], %(body_ids)s::uuid[]) AS d(member_id, body_id)
    ON CONFLICT (member_id, body_id) DO NOTHING
"""

#: The reconcile is advisory, not authoritative: scoped to members PRESENT in this
#: pull, so a member dropped from the sweep - whether they left the org or their
#: record was only transiently undecodable - keeps their leader rows rather than
#: being deprovisioned on a possibly-transient absence. A present member who stepped
#: down still loses the pairs the roster no longer names. (Removing a genuinely
#: departed leader's access is a separate, deferred concern: their stored
#: `leadership_assessment`, which the login gate reads, also persists until then.)
_DELETE_STALE_LEADER_ROWS = """
    DELETE FROM body_memberships bm
    WHERE bm.role = 'leader'
      AND bm.member_id = ANY(%(present_member_ids)s::uuid[])
      AND NOT EXISTS (
          SELECT 1
          FROM unnest(%(member_ids)s::uuid[], %(body_ids)s::uuid[]) AS d(member_id, body_id)
          WHERE d.member_id = bm.member_id AND d.body_id = bm.body_id
      )
"""


@dataclass(frozen=True)
class PullAnomaly:
    """A member whose raw chapter-leader flag disagrees with their derived roles."""

    member_id: UUID
    assessment: LeadershipAssessment


@dataclass(frozen=True)
class PullReport:
    """Counts from one roster pull, plus the members it flagged or skipped."""

    members_upserted: int
    bodies_upserted: int
    leader_rows: int
    anomalies: list[PullAnomaly]
    skipped_st_ids: list[int]


async def pull_roster(pool: AsyncConnectionPool, members: list[Member]) -> PullReport:
    """Upsert `members` into Postgres and reconcile leader rows to match the roster.

    Each distinct `Leadership` is upserted once regardless of how many members
    hold it; every present member's desired `(member, body)` leader pairs are
    then reconciled together in a single insert/delete, so a body a member
    stepped down from loses only that pair - a co-leader's row on the same body,
    or a member absent from this pull, is untouched.
    """
    body_ids: dict[Leadership, UUID] = {}
    pair_member_ids: list[UUID] = []
    pair_body_ids: list[UUID] = []
    present_member_ids: list[UUID] = []
    anomalies: list[PullAnomaly] = []
    skipped_st_ids: list[int] = []

    async with pool.connection() as conn:
        # Must be set while the connection is idle - a plain property
        # assignment is refused on an async connection.
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        async with conn.transaction():
            for member in members:
                try:
                    # A savepoint per member: a unique-constraint clash rolls back
                    # this member alone, not every good member before it.
                    async with conn.transaction():
                        assessment = assess(member.is_chapter_leader, member.leads)
                        cursor = await conn.execute(
                            _UPSERT_MEMBER,
                            {
                                "st_id": member.st_id,
                                "first_name": member.first_name,
                                "last_name": member.last_name,
                                "email": member.email,
                                "discord_id": member.discord_id,
                                "standing": member.standing,
                                "is_chapter_leader": member.is_chapter_leader,
                                "assessment": assessment,
                            },
                        )
                        row = await cursor.fetchone()
                        assert row is not None  # RETURNING on an upsert yields a row
                        member_id: UUID = row[0]
                        for leadership in member.leads:
                            body_id = body_ids.get(leadership)
                            if body_id is None:
                                body_cursor = await conn.execute(
                                    _UPSERT_BODY,
                                    {
                                        "name": leadership.name,
                                        "body_type": leadership.body_type.value,
                                    },
                                )
                                body_row = await body_cursor.fetchone()
                                assert body_row is not None  # same: upsert, RETURNING
                                body_id = body_row[0]
                                body_ids[leadership] = body_id
                            pair_member_ids.append(member_id)
                            pair_body_ids.append(body_id)
                        present_member_ids.append(member_id)
                        if assessment.is_anomalous:
                            anomalies.append(
                                PullAnomaly(member_id=member_id, assessment=assessment)
                            )
                except psycopg.errors.UniqueViolation:
                    # A duplicate email or Discord id against another member: skip
                    # this one and report it, rather than aborting every good member.
                    skipped_st_ids.append(member.st_id)

            reconcile_params = {
                "member_ids": pair_member_ids,
                "body_ids": pair_body_ids,
                "present_member_ids": present_member_ids,
            }
            await conn.execute(_INSERT_LEADER_ROWS, reconcile_params)
            await conn.execute(_DELETE_STALE_LEADER_ROWS, reconcile_params)

    return PullReport(
        members_upserted=len(present_member_ids),
        bodies_upserted=len(body_ids),
        leader_rows=len(pair_member_ids),
        anomalies=anomalies,
        skipped_st_ids=skipped_st_ids,
    )
