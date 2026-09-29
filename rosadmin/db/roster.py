"""The roster pull: upsert a Solidarity Tech roster into the local members and
leadership tables, and reconcile the leader rows to match.

The whole pull is one `REPEATABLE READ` transaction, so an unexpected mid-pull
failure rolls it all back and a retry is always safe. Each member is upserted
under its own savepoint, so a single record clashing with another's unique
email or Discord id is skipped and reported, not fatal - unless the row it
clashed with is a stale, lapsed leftover of a Solidarity Tech account merge, in
which case the merge is completed locally: the stale row is cleared and the
upsert retried. Solidarity Tech is read-only to this codebase, so the only
store a pull can affect is this one.
A known accepted edge: a record that transiently fails decode client-side is
just as invisible to the pull as a genuine absence, so it lapses the same way
and self-restores on the next clean pull. A pull that would lapse an
implausible share of the good-standing roster in one pass refuses the lapse
outright instead of applying it - see `LAPSE_FUSE_FLOOR` and
`LAPSE_FUSE_FRACTION`.

There are two entry points that can call `pull_roster` (the CLI and the admin
socket's pull route), and nothing outside this function serializes them. The
transaction takes a `pg_advisory_xact_lock` as its first statement, so a second
pull queues behind the first instead of interleaving with it under
`REPEATABLE READ` and risking a serialization failure. A queued pull that
starts once the first commits simply re-reads the roster the first already
wrote and re-applies the same upserts, which are idempotent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from rosadmin.membership.source import (
    Leadership,
    LeadershipAssessment,
    Member,
    Standing,
    assess,
)

_UPSERT_MEMBER = """
    INSERT INTO members
        (st_id, first_name, last_name, email, alternate_email, discord_user_id,
         standing, is_chapter_leader, leadership_assessment)
    VALUES
        (%(st_id)s, %(first_name)s, %(last_name)s, %(email)s, %(alternate_email)s,
         %(discord_id)s, %(standing)s, %(is_chapter_leader)s, %(assessment)s)
    ON CONFLICT (st_id) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        email = EXCLUDED.email,
        alternate_email = EXCLUDED.alternate_email,
        discord_user_id = EXCLUDED.discord_user_id,
        standing = EXCLUDED.standing,
        is_chapter_leader = EXCLUDED.is_chapter_leader,
        leadership_assessment = EXCLUDED.leadership_assessment
    RETURNING id
"""

#: The app role only holds UPDATE on the two linkage columns added for
#: chapter-leader authorization, not on `name`, so a `DO UPDATE ... RETURNING`
#: trick can't be used to fetch the id on conflict. `DO NOTHING RETURNING`
#: yields a row on insert and no row on conflict; the conflict case falls
#: back to `_SELECT_BODY_ID`.
_UPSERT_BODY = """
    INSERT INTO leadership_bodies (name, body_type)
    VALUES (%(name)s, %(body_type)s)
    ON CONFLICT (name, body_type) DO NOTHING
    RETURNING id
"""

_SELECT_BODY_ID = """
    SELECT id FROM leadership_bodies WHERE name = %(name)s AND body_type = %(body_type)s
"""

#: The default names Postgres gives the `members.email` and
#: `members.discord_user_id` UNIQUE constraints - what a `UniqueViolation`'s
#: diagnostics carry, and how `_clear_stale_merge_donor` tells the two apart.
_EMAIL_UNIQUE_CONSTRAINT = "members_email_key"
_DISCORD_UNIQUE_CONSTRAINT = "members_discord_user_id_key"

_SELECT_EMAIL_HOLDER = """
    SELECT id, standing FROM members WHERE email = %(value)s AND st_id != %(st_id)s
"""
_SELECT_DISCORD_HOLDER = """
    SELECT id, standing FROM members
    WHERE discord_user_id = %(value)s AND st_id != %(st_id)s
"""
_DELETE_MEMBER = "DELETE FROM members WHERE id = %(id)s"

#: The non-`st_id` UNIQUE columns a merge donor can be cleared on - the retry
#: bound in `_upsert_with_merge_retry`: at most one clear per column.
_MAX_MERGE_RETRIES = 2

#: The global leader reconcile: driven off parallel arrays through `unnest`
#: rather than a literal `IN (...)`/`NOT IN (...)`, which is invalid SQL when
#: the roster has no leaders at all. `unnest` on two empty arrays yields no
#: rows, so an empty roster reconciles correctly to "no leader rows".
#: `DO UPDATE`, not `DO NOTHING`: the conflicting row may be a panel-added
#: `role='member'` row, and the records naming that member a leader is a
#: promotion - the row flips to the leader role and sheds its manual-add
#: provenance, because the records own it from here on. On a row that is
#: already a leader's this re-states what is there, harmlessly.
_INSERT_LEADER_ROWS = """
    INSERT INTO body_memberships (member_id, body_id, role)
    SELECT d.member_id, d.body_id, 'leader'
    FROM unnest(%(member_ids)s::uuid[], %(body_ids)s::uuid[]) AS d(member_id, body_id)
    ON CONFLICT (member_id, body_id) DO UPDATE
        SET role = 'leader', added_by = NULL, manually_added_at = NULL
"""

#: The reconcile is advisory, not authoritative: scoped to members PRESENT in this
#: pull, so a member dropped from the sweep - whether they left the org or their
#: record was only transiently undecodable - keeps their leader rows rather than
#: being deprovisioned on a possibly-transient absence. A present member who stepped
#: down still loses the pairs the roster no longer names. (Removing a genuinely
#: departed leader's access is a separate, deferred concern: their stored
#: `leadership_assessment`, which the login gate reads, also persists until then.)
#: An arbitrary constant, unique within this database's advisory-lock keyspace -
#: nothing else in this codebase takes a `pg_advisory_xact_lock`. Held only for
#: the duration of the pull's transaction, then released automatically.
_PULL_LOCK_KEY = 0x_5052_4C4C

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

#: Members present in the database but absent from this pull lost their
#: records upstream - deleted, or moved out of the chapter - which is
#: functionally a lapse: no access, and restored the same way if they
#: return. Members this pull SKIPPED on a unique-constraint clash are
#: excluded by st_id: they are present upstream, just unstorable, and a
#: data bug must not cost them their standing.
_LAPSE_ABSENT_MEMBERS = """
    UPDATE members
    SET standing = 'lapsed'
    WHERE standing = 'good_standing'
      AND NOT (id = ANY(%(present_member_ids)s::uuid[]))
      AND NOT (st_id = ANY(%(skipped_st_ids)s::bigint[]))
"""

#: A pull that would lapse more than this many currently good-standing members -
#: or more than LAPSE_FUSE_FRACTION of them, whichever is larger - is refusing an
#: implausible mass absence. Genuine absences (a member deleted from or moved out
#: of Solidarity Tech) are a trickle; a large one means the pull itself is broken
#: (an empty or truncated upstream, a decoder-wide failure), so the lapse is
#: refused rather than stripping standing from most of the roster. Mirrors the
#: reconcile sweep's removal fuse.
LAPSE_FUSE_FLOOR = 5
LAPSE_FUSE_FRACTION = 0.10

_COUNT_LAPSE_CANDIDATES = """
    SELECT
        count(*) FILTER (
            WHERE NOT (id = ANY(%(present_member_ids)s::uuid[]))
              AND NOT (st_id = ANY(%(skipped_st_ids)s::bigint[]))
        ) AS would_lapse,
        count(*) AS good_standing_total
    FROM members
    WHERE standing = 'good_standing'
"""


@dataclass(frozen=True)
class PullAnomaly:
    """A member whose raw chapter-leader flag disagrees with their derived roles."""

    member_id: UUID
    assessment: LeadershipAssessment


@dataclass(frozen=True)
class PullReport:
    """Counts from one roster pull, plus the members it flagged, skipped, or merged."""

    members_upserted: int
    bodies_upserted: int
    leader_rows: int
    anomalies: list[PullAnomaly]
    skipped_st_ids: list[int]
    merged_st_ids: list[int]
    absent_lapsed: int
    lapse_refused: int


#: Attempts a pull makes before letting a serialization failure propagate. A
#: queued pull can lose exactly one race - its snapshot predates the lock
#: holder's commit - so the second attempt, taken with the lock already held,
#: sees a fresh snapshot and succeeds; more than one extra try means something
#: other than pull-vs-pull contention is at work and deserves the error.
_PULL_ATTEMPTS = 3


async def pull_roster(pool: AsyncConnectionPool, members: list[Member]) -> PullReport:
    """Upsert `members` into Postgres and reconcile leader rows to match the roster.

    Each distinct `Leadership` is upserted once regardless of how many members
    hold it; every present member's desired `(member, body)` leader pairs are
    then reconciled together in a single insert/delete, so a body a member
    stepped down from loses only that pair - a co-leader's row on the same body,
    or a member absent from this pull, is untouched.

    Concurrent pulls serialize on the advisory lock, but the lock statement
    itself pins the waiting transaction's snapshot before it blocks, so the
    queued pull can still wake to a stale view and fail to serialize. The whole
    transaction rolls back cleanly in that case, and this retries it - the
    always-safe retry the transaction shape exists to make possible.
    """
    for attempt in range(1, _PULL_ATTEMPTS + 1):
        try:
            return await _pull_once(pool, members)
        except psycopg.errors.SerializationFailure:
            if attempt == _PULL_ATTEMPTS:
                raise
    raise AssertionError("unreachable: every loop path returns or raises")


async def _pull_once(pool: AsyncConnectionPool, members: list[Member]) -> PullReport:
    """One pull attempt: the whole-roster transaction `pull_roster` retries."""
    async with pool.connection() as conn:
        # Must be set while the connection is idle - a plain property
        # assignment is refused on an async connection. The finally matters:
        # this is a pooled connection (the admin socket's pull runs on the
        # service's shared pool), and the isolation level is a connection
        # attribute that survives release, so without the restore every later
        # borrower of this connection would silently run REPEATABLE READ.
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        try:
            return await _pull_in(conn, members)
        finally:
            await conn.set_isolation_level(None)


async def _upsert_one(
    conn: psycopg.AsyncConnection,
    member: Member,
    body_ids: dict[Leadership, UUID],
    pair_member_ids: list[UUID],
    pair_body_ids: list[UUID],
) -> tuple[UUID, LeadershipAssessment]:
    """Upsert one member and its leadership pairs under a fresh savepoint.

    Raises `psycopg.errors.UniqueViolation`, uncaught, on a colliding email or
    Discord id - the caller decides whether that is a stale merge donor worth
    clearing and retrying, or a genuine clash to skip and report.
    """
    async with conn.transaction():
        assessment = assess(member.is_chapter_leader, member.leads)
        cursor = await conn.execute(
            _UPSERT_MEMBER,
            {
                "st_id": member.st_id,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "email": member.email,
                "alternate_email": member.alternate_email,
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
                body_params = {
                    "name": leadership.name,
                    "body_type": leadership.body_type.value,
                }
                body_cursor = await conn.execute(_UPSERT_BODY, body_params)
                body_row = await body_cursor.fetchone()
                if body_row is None:
                    body_cursor = await conn.execute(_SELECT_BODY_ID, body_params)
                    body_row = await body_cursor.fetchone()
                assert body_row is not None  # inserted or already present
                body_id = body_row[0]
                body_ids[leadership] = body_id
            pair_member_ids.append(member_id)
            pair_body_ids.append(body_id)
        return member_id, assessment


async def _clear_stale_merge_donor(
    conn: psycopg.AsyncConnection,
    member: Member,
    violation: psycopg.errors.UniqueViolation,
) -> bool:
    """Delete the lapsed row holding the email or Discord id `member` now carries.

    Solidarity Tech resolves a duplicate account by deleting the duplicate's id
    and folding its identity onto the survivor. Locally, the duplicate's own row
    only lapses on that pull, same as any other absence, and keeps holding the
    value until the survivor's own upsert collides with it - which is the only
    signal this side ever gets that a merge happened. Returns False, leaving
    every row untouched, for anything else: a live holder is a genuine,
    unresolved clash between two currently-present members and must not be
    silently overwritten, and a constraint this doesn't recognize is left for
    the ordinary skip-and-report path.
    """
    constraint = violation.diag.constraint_name
    if constraint == _EMAIL_UNIQUE_CONSTRAINT:
        query, value = _SELECT_EMAIL_HOLDER, member.email
    elif constraint == _DISCORD_UNIQUE_CONSTRAINT:
        query, value = _SELECT_DISCORD_HOLDER, member.discord_id
    else:
        return False
    cursor = await conn.execute(query, {"value": value, "st_id": member.st_id})
    row = await cursor.fetchone()
    if row is None or row[1] is not Standing.Lapsed:
        return False
    await conn.execute(_DELETE_MEMBER, {"id": row[0]})
    return True


async def _upsert_with_merge_retry(
    conn: psycopg.AsyncConnection,
    member: Member,
    body_ids: dict[Leadership, UUID],
    pair_member_ids: list[UUID],
    pair_body_ids: list[UUID],
) -> tuple[UUID, LeadershipAssessment, bool] | None:
    """`_upsert_one`, clearing at most one stale email donor and one stale
    Discord-id donor before giving up.

    The bool is whether a donor was cleared to get there (false on the
    ordinary, no-collision path). `None` is a skip: the collision survived
    every clear attempt, so it is a genuine clash, not a merge.
    """
    cleared = False
    for attempt in range(_MAX_MERGE_RETRIES + 1):
        try:
            member_id, assessment = await _upsert_one(
                conn, member, body_ids, pair_member_ids, pair_body_ids
            )
            return member_id, assessment, cleared
        except psycopg.errors.UniqueViolation as violation:
            if attempt == _MAX_MERGE_RETRIES or not await _clear_stale_merge_donor(
                conn, member, violation
            ):
                return None
            cleared = True
    return None  # unreachable: the loop always returns


async def _pull_in(conn: psycopg.AsyncConnection, members: list[Member]) -> PullReport:
    """The pull's transaction body, on a connection already at REPEATABLE READ."""
    body_ids: dict[Leadership, UUID] = {}
    pair_member_ids: list[UUID] = []
    pair_body_ids: list[UUID] = []
    present_member_ids: list[UUID] = []
    anomalies: list[PullAnomaly] = []
    skipped_st_ids: list[int] = []
    merged_st_ids: list[int] = []

    async with conn.transaction():
        # Serializes concurrent pulls: the second call blocks here until the
        # first's transaction ends, rather than interleaving under
        # REPEATABLE READ and risking a serialization failure.
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_PULL_LOCK_KEY,))
        for member in members:
            result = await _upsert_with_merge_retry(
                conn, member, body_ids, pair_member_ids, pair_body_ids
            )
            if result is None:
                # A duplicate email or Discord id against another live member:
                # skip this one and report it, rather than aborting every good
                # member before it.
                skipped_st_ids.append(member.st_id)
                continue
            member_id, assessment, cleared = result
            if cleared:
                merged_st_ids.append(member.st_id)
            present_member_ids.append(member_id)
            if assessment.is_anomalous:
                anomalies.append(
                    PullAnomaly(member_id=member_id, assessment=assessment)
                )

        reconcile_params = {
            "member_ids": pair_member_ids,
            "body_ids": pair_body_ids,
            "present_member_ids": present_member_ids,
        }
        await conn.execute(_INSERT_LEADER_ROWS, reconcile_params)
        await conn.execute(_DELETE_STALE_LEADER_ROWS, reconcile_params)

        lapse_params = {
            "present_member_ids": present_member_ids,
            "skipped_st_ids": skipped_st_ids,
        }
        count_cursor = await conn.execute(_COUNT_LAPSE_CANDIDATES, lapse_params)
        count_row = await count_cursor.fetchone()
        assert count_row is not None
        would_lapse, good_standing_total = count_row
        budget = max(
            LAPSE_FUSE_FLOOR, math.ceil(good_standing_total * LAPSE_FUSE_FRACTION)
        )
        if would_lapse > budget:
            # An implausible mass absence: refuse the lapse and report it so the
            # caller stops and is seen, rather than stripping standing wholesale.
            absent_lapsed = 0
            lapse_refused = would_lapse
        else:
            lapse_cursor = await conn.execute(_LAPSE_ABSENT_MEMBERS, lapse_params)
            absent_lapsed = lapse_cursor.rowcount
            lapse_refused = 0

    return PullReport(
        members_upserted=len(present_member_ids),
        bodies_upserted=len(body_ids),
        leader_rows=len(pair_member_ids),
        anomalies=anomalies,
        skipped_st_ids=skipped_st_ids,
        merged_st_ids=merged_st_ids,
        absent_lapsed=absent_lapsed,
        lapse_refused=lapse_refused,
    )
