"""The data layer against a real ephemeral Postgres: sessions and audit."""

from __future__ import annotations

import asyncio
import sys
import uuid

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.audit import PostgresAuditSink
from rosadmin.db.sessions import PostgresSessionStore
from rosadmin.membership.source import Standing
from rosadmin.web.sessions import IDLE_TIMEOUT, LeaderContext

pytestmark = pytest.mark.integration

_LEADER = LeaderContext(
    member_id=uuid.uuid4(), display_name="Ralsei", managed_group_ids=frozenset()
)


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    # psycopg's async pool cannot run under Windows' default ProactorEventLoop;
    # it needs a selector loop. Linux (dev laptop and CI alike) keeps the
    # default policy untouched.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


async def _pool(dsn: str):
    pool = make_pool(dsn)
    await pool.open()
    return pool


async def test_session_survives_a_fresh_store(database) -> None:
    # Ralsei's session, minted by one store, resolves through a second store
    # against the same database - it outlived the process, which the in-memory
    # stand-in never could.
    pool = await _pool(database.app_dsn)
    try:
        token = await PostgresSessionStore(pool).create(_LEADER)
        assert await PostgresSessionStore(pool).resolve(token) == _LEADER
    finally:
        await pool.close()


async def test_managed_group_ids_round_trip_preserves_uuids(database) -> None:
    # A leader managing real bodies: the uuid[] column must come back as UUID
    # objects (not strings) and preserve the exact set - an empty-set round trip
    # would hide a type-mapping bug.
    managed = frozenset({uuid.uuid4(), uuid.uuid4()})
    leader = LeaderContext(
        member_id=uuid.uuid4(), display_name="Ralsei", managed_group_ids=managed
    )
    pool = await _pool(database.app_dsn)
    try:
        store = PostgresSessionStore(pool)
        resolved = await store.resolve(await store.create(leader))
    finally:
        await pool.close()
    assert resolved is not None
    assert resolved.managed_group_ids == managed
    assert all(isinstance(gid, uuid.UUID) for gid in resolved.managed_group_ids)


async def test_expired_session_resolves_to_none(database) -> None:
    pool = await _pool(database.app_dsn)
    try:
        store = PostgresSessionStore(pool)
        token = await store.create(_LEADER)
        # Push last_seen past the idle window; Susie's stale session is refused.
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET last_seen_at = now() - %s - interval '1 minute'",
                (IDLE_TIMEOUT,),
            )
        assert await store.resolve(token) is None
    finally:
        await pool.close()


async def test_audit_row_is_written_and_pseudonymized(database) -> None:
    pool = await _pool(database.app_dsn)
    try:
        await PostgresAuditSink(pool, b"test-key").record(
            "login", actor="member-123", detail={"method": "fake"}
        )
    finally:
        await pool.close()
    with psycopg.connect(database.superuser_dsn) as conn:
        row = conn.execute(
            "SELECT action, actor_hmac, detail FROM audit_log"
        ).fetchone()
    assert row is not None
    action, actor_hmac, detail = row
    assert action == "login"
    assert actor_hmac != "member-123"  # pseudonymized, raw id absent
    assert detail == {"method": "fake"}


async def test_member_standing_round_trips_as_enum(database) -> None:
    # Proves the pool's enum registration: the column comes back as Standing.
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s)",
            (7, "ralsei@example.com", "good_standing"),
        )
    pool = await _pool(database.app_dsn)
    try:
        async with pool.connection() as conn:
            cursor = await conn.execute("SELECT standing FROM members WHERE st_id = 7")
            row = await cursor.fetchone()
    finally:
        await pool.close()
    assert row is not None
    (standing,) = row
    assert standing is Standing.GOOD_STANDING
