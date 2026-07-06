"""The `RateLimiter` backed by `rate_limit_counters`.

One statement does the whole job: insert the window row at 1, or bump the existing
count, returning the post-increment value. Atomic, so concurrent hits cannot both
read a stale count.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


class PostgresRateLimiter:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def hit(self, bucket: str) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO rate_limit_counters (bucket, window_start, count) "
                "VALUES (%s, date_trunc('minute', now()), 1) "
                "ON CONFLICT (bucket, window_start) "
                "DO UPDATE SET count = rate_limit_counters.count + 1 "
                "RETURNING count",
                (bucket,),
            )
            row = await cursor.fetchone()
        assert row is not None  # RETURNING on an upsert always yields a row
        return row[0]
