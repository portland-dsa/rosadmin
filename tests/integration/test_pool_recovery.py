from __future__ import annotations

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from rosadmin.db import make_pool

pytestmark = pytest.mark.integration


def _terminate_app_backends(superuser_dsn: str, app_dsn: str) -> None:
    """Kill every backend the app role holds - the server side of the Postgres
    restart that an unattended upgrade performs under the running service."""
    app_user = conninfo_to_dict(app_dsn)["user"]
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE usename = %s AND pid <> pg_backend_pid()",
            (app_user,),
        )


async def test_pool_recovers_after_its_backends_are_terminated(database):
    # Eggman restarts the cluster out from under the pool; the next checkout must
    # come back with a live connection, not raise AdminShutdown off a dead one.
    async with make_pool(database.app_dsn) as pool:
        await pool.open()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        _terminate_app_backends(database.superuser_dsn, database.app_dsn)
        async with pool.connection() as conn:
            cursor = await conn.execute("SELECT 1")
            assert await cursor.fetchone() == (1,)
