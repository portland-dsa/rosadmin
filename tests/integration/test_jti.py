from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import AsyncConnectionPool

from rosadmin.db.jti import PostgresJtiCache

pytestmark = pytest.mark.integration

_LATER = datetime.now(timezone.utc) + timedelta(seconds=30)


async def test_a_jti_is_single_use_against_real_postgres(database):
    # Annotate before construction so pyright infers the pool's full generic
    # type: a bare `AsyncConnectionPool(...)` only picks up its default type
    # parameters in an expected-type context, which the constructor call does
    # not supply - the same reason `rosadmin.db.make_pool` carries a bare
    # return annotation.
    pool: AsyncConnectionPool = AsyncConnectionPool(database.app_dsn, open=False)
    async with pool:
        await pool.open()
        cache = PostgresJtiCache(pool)
        assert await cache.claim("only-once", _LATER) is True
        assert await cache.claim("only-once", _LATER) is False
