"""A Solidarity Tech account merge deletes the duplicate id upstream and folds
its identity - email, Discord id, or both - onto the survivor. Locally, the
duplicate's row only lapses on the pull that first notices its absence, same
as any other absence, and keeps holding the value until the survivor's own
upsert collides with it. `pull_roster` must complete that merge (clear the
stale lapsed row, then claim the value) rather than skip the survivor forever.

A genuine clash between two currently-present members - not a merge, just bad
data - must still be left alone and reported, never silently resolved.
"""

from __future__ import annotations

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.roster import pull_roster
from rosadmin.membership.source import Email, Member, Standing
from tests.support.pg import one_row

pytestmark = pytest.mark.integration


def _member(
    st_id: int, *, email: str, discord_id: int | None, standing: Standing
) -> Member:
    return Member(
        st_id=st_id,
        email=Email(email),
        alternate_email=None,
        standing=standing,
        discord_id=discord_id,
        first_name=None,
        last_name=None,
        alternate_name=None,
        is_chapter_leader=False,
        leads=frozenset(),
    )


@pytest.mark.parametrize("colliding_field", ["email", "discord_id"])
async def test_survivor_reclaims_a_stale_lapsed_duplicates_identity(
    database, colliding_field
) -> None:
    duplicate = _member(
        1, email="susie@example.com", discord_id=555, standing=Standing.Lapsed
    )
    survivor = _member(
        2,
        email="susie@example.com"
        if colliding_field == "email"
        else "ralsei@example.com",
        discord_id=555 if colliding_field == "discord_id" else 999,
        standing=Standing.GoodStanding,
    )

    pool = make_pool(database.app_dsn)
    await pool.open()
    try:
        await pull_roster(pool, [duplicate])
        report = await pull_roster(pool, [survivor])
    finally:
        await pool.close()

    assert report.members_upserted == 1
    assert report.skipped_st_ids == []
    assert report.merged_st_ids == [2]

    with psycopg.connect(database.superuser_dsn) as conn:
        st_ids = one_row(conn.execute("SELECT array_agg(st_id) FROM members"))[0]
    assert st_ids == [2]


async def test_two_separate_stale_donors_are_both_cleared(database) -> None:
    """One donor holds the email, a different one holds the Discord id - a
    merge that consolidated two different prior duplicates onto one survivor.
    The bounded retry must clear both, not give up after the first."""
    email_donor = _member(
        1, email="susie@example.com", discord_id=111, standing=Standing.Lapsed
    )
    discord_donor = _member(
        2, email="noelle@example.com", discord_id=555, standing=Standing.Lapsed
    )
    survivor = _member(
        3, email="susie@example.com", discord_id=555, standing=Standing.GoodStanding
    )

    pool = make_pool(database.app_dsn)
    await pool.open()
    try:
        await pull_roster(pool, [email_donor, discord_donor])
        report = await pull_roster(pool, [survivor])
    finally:
        await pool.close()

    assert report.members_upserted == 1
    assert report.skipped_st_ids == []
    assert report.merged_st_ids == [3]

    with psycopg.connect(database.superuser_dsn) as conn:
        st_ids = one_row(conn.execute("SELECT array_agg(st_id) FROM members"))[0]
    assert st_ids == [3]


async def test_live_collision_is_not_silently_resolved(database) -> None:
    first = _member(
        1, email="susie@example.com", discord_id=555, standing=Standing.GoodStanding
    )
    second = _member(
        2, email="ralsei@example.com", discord_id=555, standing=Standing.GoodStanding
    )

    pool = make_pool(database.app_dsn)
    await pool.open()
    try:
        report = await pull_roster(pool, [first, second])
    finally:
        await pool.close()

    assert report.skipped_st_ids == [2]
    assert report.merged_st_ids == []

    with psycopg.connect(database.superuser_dsn) as conn:
        st_ids = one_row(conn.execute("SELECT array_agg(st_id) FROM members"))[0]
    assert st_ids == [1]
