"""Two overlapping roster pulls against a real engine.

`pull_roster` runs under `REPEATABLE READ`, which can raise a serialization
failure when two transactions overlap and write the same rows. The advisory
lock the pull takes as its first statement serializes them instead - the
second pull queues behind the first rather than interleaving with it - so
both calls below must complete without error and leave exactly one copy of
the roster behind.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.roster import pull_roster
from rosadmin.membership.source import BodyType, Email, Leadership, Member, Standing
from tests.support.pg import one_row

pytestmark = pytest.mark.integration

_MEMBERS = [
    Member(
        st_id=1,
        email=Email("susie@example.com"),
        alternate_email=None,
        standing=Standing.GoodStanding,
        discord_id=None,
        first_name="Susie",
        last_name=None,
        alternate_name=None,
        is_chapter_leader=True,
        leads=frozenset({Leadership(body_type=BodyType.Committee, name="wreckers")}),
    ),
    Member(
        st_id=2,
        email=Email("ralsei@example.com"),
        alternate_email=None,
        standing=Standing.GoodStanding,
        discord_id=None,
        first_name="Ralsei",
        last_name=None,
        alternate_name=None,
        is_chapter_leader=False,
        leads=frozenset(),
    ),
]


async def test_concurrent_pulls_serialize_instead_of_failing(database) -> None:
    pool_a = make_pool(database.app_dsn)
    pool_b = make_pool(database.app_dsn)
    await pool_a.open()
    await pool_b.open()
    try:
        report_a, report_b = await asyncio.gather(
            pull_roster(pool_a, _MEMBERS), pull_roster(pool_b, _MEMBERS)
        )
    finally:
        await pool_a.close()
        await pool_b.close()

    assert report_a.members_upserted == 2
    assert report_b.members_upserted == 2

    with psycopg.connect(database.superuser_dsn) as conn:
        (member_count,) = one_row(conn.execute("SELECT count(*) FROM members"))
        (body_count,) = one_row(conn.execute("SELECT count(*) FROM leadership_bodies"))
        (leader_row_count,) = one_row(
            conn.execute("SELECT count(*) FROM body_memberships WHERE role = 'leader'")
        )
    assert member_count == 2
    assert body_count == 1
    assert leader_row_count == 1
