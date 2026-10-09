"""Addresses Google refuses to hold, the clock that lets them back in, and the
marker that arms the fuse over them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from psycopg_pool import AsyncConnectionPool

from rosadmin.group_sync import SyncOutcome
from rosadmin.membership.source import Email

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

_REFUSED_ADDRESSES = """
    SELECT address, observed_at > now() - %(retry_after)s AS live
    FROM unmirrorable_addresses
"""

_RECORD_ADDRESS = """
    INSERT INTO unmirrorable_addresses (address, reason)
    VALUES (%(address)s, %(reason)s)
    ON CONFLICT (address) DO UPDATE
        SET reason = EXCLUDED.reason, observed_at = now()
"""

_READ_BOOTSTRAP = "SELECT bootstrapped_refusal_learning FROM bootstrap_state"
_SET_BOOTSTRAP = "UPDATE bootstrap_state SET bootstrapped_refusal_learning = true"


@dataclass(frozen=True)
class RefusedAddresses:
    """Every address Google has refused, split on whether the refusal still holds.

    Both sets are keyed as the sweep compares. A `lapsed` address has been refused
    before - which is what tells a retry apart from news - but is due to be asked
    about again.
    """

    live: frozenset[str]
    lapsed: frozenset[str]


async def refused_addresses(pool: AsyncConnectionPool) -> RefusedAddresses:
    async with pool.connection() as conn:
        cursor = await conn.execute(_REFUSED_ADDRESSES, {"retry_after": RETRY_AFTER})
        rows = await cursor.fetchall()
    return RefusedAddresses(
        live=frozenset(address.lower() for address, live in rows if live),
        lapsed=frozenset(address.lower() for address, live in rows if not live),
    )


async def record_unmirrorable(
    pool: AsyncConnectionPool, address: Email, reason: SyncOutcome
) -> None:
    """Remember that Google refused this address, and restart its clock."""
    async with pool.connection() as conn:
        await conn.execute(
            _RECORD_ADDRESS, {"address": address.lower(), "reason": reason.value}
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
