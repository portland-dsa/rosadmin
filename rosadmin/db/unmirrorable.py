"""Addresses Google refuses to hold, the clock that lets them back in, and the
marker that arms the fuse over them.
"""

from __future__ import annotations

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

_LIVE_ADDRESSES = """
    SELECT address
    FROM unmirrorable_addresses
    WHERE observed_at > now() - %(retry_after)s
"""

_RECORD_ADDRESS = """
    INSERT INTO unmirrorable_addresses (address, reason)
    VALUES (%(address)s, %(reason)s)
    ON CONFLICT (address) DO UPDATE
        SET reason = EXCLUDED.reason, observed_at = now()
"""

_READ_BOOTSTRAP = "SELECT bootstrapped_refusal_learning FROM bootstrap_state"
_SET_BOOTSTRAP = "UPDATE bootstrap_state SET bootstrapped_refusal_learning = true"


async def unmirrorable_addresses(
    pool: AsyncConnectionPool, *, retry_after: timedelta = RETRY_AFTER
) -> set[str]:
    """The addresses still inside their retry window, keyed as the sweep compares."""
    async with pool.connection() as conn:
        cursor = await conn.execute(_LIVE_ADDRESSES, {"retry_after": retry_after})
        rows = await cursor.fetchall()
    return {address.lower() for (address,) in rows}


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
