"""The data layer against a real ephemeral Postgres: sessions and audit."""

from __future__ import annotations

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.audit import PostgresAuditSink
from rosadmin.db.sessions import PostgresSessionStore
from rosadmin.membership.source import LeadershipAssessment, Standing
from rosadmin.sso import DiscordUserId
from rosadmin.web.sessions import IDLE_TIMEOUT, Principal

pytestmark = pytest.mark.integration

_PRINCIPAL = Principal(discord_id=DiscordUserId("123456789012345678"))


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
        token = await PostgresSessionStore(pool).create(_PRINCIPAL)
        assert await PostgresSessionStore(pool).resolve(token) == _PRINCIPAL
    finally:
        await pool.close()


async def test_expired_session_resolves_to_none(database) -> None:
    pool = await _pool(database.app_dsn)
    try:
        store = PostgresSessionStore(pool)
        token = await store.create(_PRINCIPAL)
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
    assert standing is Standing.GoodStanding


async def test_leadership_assessment_round_trips_and_defaults(database) -> None:
    # Mirrors the standing round-trip above, extended for the second enum:
    # Kris's row states the assessment explicitly, Noelle's leaves it out and
    # falls to the column default rather than erroring.
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO members (st_id, email, standing, leadership_assessment)"
            " VALUES (%s, %s, %s, %s)",
            (8, "kris@example.com", "good_standing", "leader"),
        )
        conn.execute(
            "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s)",
            (9, "noelle@example.com", "good_standing"),
        )
    pool = await _pool(database.app_dsn)
    try:
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT st_id, leadership_assessment FROM members ORDER BY st_id"
            )
            rows = await cursor.fetchall()
    finally:
        await pool.close()
    assessments = dict(rows)
    assert assessments[8] is LeadershipAssessment.Leader
    assert assessments[9] is LeadershipAssessment.NonLeader
