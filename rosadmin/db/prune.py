"""Housekeeping: delete the short-lived auth rows whose
windows have closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class PruneReport:
    """How many spent rows one housekeeping pass removed."""

    jti: int
    rate_limit: int


async def prune_expired(pool: AsyncConnectionPool) -> PruneReport:
    """Delete expired jti and closed rate-limit windows. Idempotent: a run with
    nothing to remove returns zeroes.

    A rate-limit window is keyed to the minute it opened; an hour is far past any
    window the limiter enforces, so a counter older than that is certainly spent.
    """
    async with pool.connection() as conn:
        jti = await conn.execute("DELETE FROM jti_replay WHERE expires_at < now()")
        rate = await conn.execute(
            "DELETE FROM rate_limit_counters "
            "WHERE window_start < now() - interval '1 hour'"
        )
    return PruneReport(jti=jti.rowcount, rate_limit=rate.rowcount)
