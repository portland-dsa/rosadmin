"""The `JtiCache` backed by the `jti_replay` table.

Single-use is the table's `PRIMARY KEY`, enforced by one atomic
`INSERT ... ON CONFLICT DO NOTHING RETURNING` - never a check-then-insert, which
would let two concurrent redemptions of the same assertion both win.
"""

from __future__ import annotations

from datetime import datetime

from psycopg_pool import AsyncConnectionPool


class PostgresJtiCache:
    """The `JtiCache` over Postgres."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def claim(self, jti: str, expires_at: datetime) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO jti_replay (jti, expires_at) VALUES (%s, %s) "
                "ON CONFLICT (jti) DO NOTHING RETURNING jti",
                (jti, expires_at),
            )
            row = await cursor.fetchone()
        return row is not None
