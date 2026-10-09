"""Tracks information about emails synced to Google (statuses, refusals, etc)."""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from rosadmin.group_sync import EmailStatuses, SyncOutcome
from rosadmin.membership.source import Email

logger = logging.getLogger(__name__)

#: How long a refusal is trusted before the address is offered again. The fact it
#: records - no Google account, or a deleted one - changes when the member makes an
#: account, and nothing tells us when that happens, so the only way to find out is
#: to try. Long enough that a sweep every four hours is not re-failing several
#: hundred addresses; short enough that a member who fixes their account waits a
#: season rather than forever.
RETRY_AFTER = timedelta(days=90)

#: How many lapsed refusals one run offers to Google again. The first run writes a
#: whole standing cohort down at once, so the whole cohort lapses at once a season
#: later; asking about every one of them in a single run is hundreds of doomed
#: inserts. A run retries this many and leaves the rest withheld for the next, and
#: since each refusal Google repeats restarts that address's clock, the cohort comes
#: back spread across a few days rather than in one lump.
RETRIES_PER_RUN = 25

#: How many refusals one armed run may write down. A steady state meets a
#: handful - a member joins carrying an address with no Google account behind it -
#: so a run meeting dozens is not learning about the roster, it is learning that
#: something outside it has changed: a scope withdrawn, the security label
#: misapplied, Google answering 412 to everything. A batch past this ceiling is
#: refused wholesale rather than recorded, on the same principle as the removal
#: fuse and the creation tripwire: the point of a fuse is that it will not do the
#: thing, and a fuse that merely reported afterwards would leave the roster
#: suppressed for a season and go quiet on the very next run, having nothing left
#: to learn.
REFUSAL_FUSE_CEILING = 25

_ACCEPTED = "accepted"

_STATUSES = """
    SELECT address, status, observed_at,
           observed_at > now() - %(retry_after)s AS live
    FROM google_email_status
    WHERE %(addresses)s::text[] IS NULL OR address = ANY(%(addresses)s)
"""

_RECORD_STATUS = """
    INSERT INTO google_email_status (address, status)
    VALUES (%(address)s, %(status)s)
    ON CONFLICT (address) DO UPDATE
        SET status = EXCLUDED.status, observed_at = now()
"""

_READ_BOOTSTRAP = "SELECT bootstrapped_refusal_learning FROM bootstrap_state"
_SET_BOOTSTRAP = "UPDATE bootstrap_state SET bootstrapped_refusal_learning = true"


async def email_statuses(
    pool: AsyncConnectionPool, addresses: Collection[str] | None = None
) -> EmailStatuses:
    """Every recorded address's status, or only those among `addresses`."""
    params = {
        "retry_after": RETRY_AFTER,
        "addresses": None if addresses is None else [a.lower() for a in addresses],
    }
    async with pool.connection() as conn:
        cursor = await conn.execute(_STATUSES, params)
        rows = await cursor.fetchall()
    accepted: set[str] = set()
    refused_at: dict[str, datetime] = {}
    live: set[str] = set()
    for address, status, observed_at, is_live in rows:
        if status == _ACCEPTED:
            accepted.add(address)
            continue
        refused_at[address] = observed_at
        if is_live:
            live.add(address)
    return EmailStatuses(
        accepted=frozenset(accepted), refused_at=refused_at, live=frozenset(live)
    )


async def _record(pool: AsyncConnectionPool, address: Email, status: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            _RECORD_STATUS, {"address": address.lower(), "status": status}
        )


async def remember_accepted(
    pool: AsyncConnectionPool, addresses: Collection[Email]
) -> None:
    """Record each address as accepted, best effort: a write that fails is logged,
    and only leaves that address to be recorded on its next success."""
    for address in addresses:
        try:
            await _record(pool, address, _ACCEPTED)
        except psycopg.Error as error:
            logger.error(
                "could not record an accepted address: %s, sqlstate %s",
                type(error).__name__,
                error.sqlstate,
            )


async def is_refusal_learning_bootstrapped(pool: AsyncConnectionPool) -> bool:
    """Whether a run has already met the standing cohort of refused addresses."""
    async with pool.connection() as conn:
        cursor = await conn.execute(_READ_BOOTSTRAP)
        row = await cursor.fetchone()
        assert row is not None  # the migration seeds exactly one row
        return row[0]


async def mark_refusal_learning_bootstrapped(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(_SET_BOOTSTRAP)


@dataclass(frozen=True)
class Refusal:
    """One address Google refused, and the member and group it was refused for.

    Held rather than written the moment it is met: a refusal is only believable
    once the group it was refused on is known to still exist, and only recordable
    once the size of the run's whole batch is known.
    """

    address: Email
    member_id: UUID
    group_email: Email
    outcome: SyncOutcome


@dataclass(frozen=True)
class RefusalReport:
    """What one run did with the refusals Google issued it.

    `received` counts them as Google gave them, so no failure of the store can
    quiet what reads it; `retries` is how many of those were lapsed refusals asked
    about again. `recorded` is what actually landed. `refused` is the fuse saying
    no - the batch was too large to believe, and none of it was written.
    """

    received: int
    retries: int
    recorded: int
    refused: int

    @property
    def has_failures(self) -> bool:
        return self.refused > 0 or self.recorded < self.received - self.refused


async def commit_refusals(
    pool: AsyncConnectionPool,
    refusals: list[Refusal],
    *,
    known: frozenset[str],
    dry_run: bool,
) -> RefusalReport:
    """Write down what Google refused this run."""
    received = len(refusals)
    news = sum(1 for refusal in refusals if refusal.address.lower() not in known)
    retries = received - news
    if dry_run:
        if received > 0:
            logger.info("dry-run: would record %d refused addresses", received)
        return RefusalReport(received=received, retries=retries, recorded=0, refused=0)
    bootstrapped = await is_refusal_learning_bootstrapped(pool)
    if bootstrapped and news > REFUSAL_FUSE_CEILING:
        logger.error(
            "refusal fuse: google refused %d addresses it had not refused before, "
            "past the ceiling of %d; recording none of them.",
            news,
            REFUSAL_FUSE_CEILING,
        )
        return RefusalReport(
            received=received, retries=retries, recorded=0, refused=received
        )
    recorded = 0
    for refusal in refusals:
        if await _remember_refusal(pool, refusal):
            recorded += 1
    if not bootstrapped and recorded > 0:
        await mark_refusal_learning_bootstrapped(pool)
    return RefusalReport(
        received=received, retries=retries, recorded=recorded, refused=0
    )


async def _remember_refusal(pool: AsyncConnectionPool, refusal: Refusal) -> bool:
    """Write one refusal down, and never let that write end the sweep.

    Like the audit row beside it, this records something that has already
    happened out at Google. A database that cannot take it down is worth an
    operator's attention, but it is not worth abandoning what the run has not
    reached yet: the unrecorded address is simply offered - and refused - again
    next run, which is exactly where it started. Answers whether the row landed,
    because a refusal that was not written down is one the next run must go and
    ask about again, and the report says so.

    The failure is named by its class and SQLSTATE, never by the server's own
    message: Postgres puts the whole offending row in the detail of a constraint
    violation, and that row carries the member's address.
    """
    try:
        await _record(pool, refusal.address, refusal.outcome.value)
    except psycopg.Error as error:
        logger.error(
            "sweep: could not record google's refusal of member %s on %s (%s): "
            "%s, sqlstate %s",
            refusal.member_id,
            refusal.group_email,
            refusal.outcome.value,
            type(error).__name__,
            error.sqlstate,
        )
        return False
    logger.info(
        "sweep: google refuses member %s on %s (%s); "
        "the address will not be offered again this window",
        refusal.member_id,
        refusal.group_email,
        refusal.outcome.value,
    )
    return True
