from __future__ import annotations

import pytest
from psycopg_pool import AsyncConnectionPool

from rosadmin.db.rate_limit import PostgresRateLimiter

pytestmark = pytest.mark.integration


async def test_counts_increment_atomically(database):
    # Annotate before construction so pyright infers the pool's full generic
    # type - see the same note in tests/integration/test_jti.py.
    pool: AsyncConnectionPool = AsyncConnectionPool(database.app_dsn, open=False)
    async with pool:
        await pool.open()
        limiter = PostgresRateLimiter(pool)
        first = await limiter.hit("auth:/begin:9.9.9.9")
        second = await limiter.hit("auth:/begin:9.9.9.9")
    assert (first, second) == (1, 2)
