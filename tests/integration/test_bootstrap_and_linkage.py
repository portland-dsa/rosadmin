"""The bootstrap marker and address-unique body linkage, against a real
Postgres: the marker starts false and flips, and the two `UNIQUE` constraints
turn a shared address into a typed `LinkTaken` rather than silent corruption.
"""

from __future__ import annotations

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.directory import (
    LinkTaken,
    all_bodies,
    is_group_provisioning_bootstrapped,
    mark_group_provisioning_bootstrapped,
    set_body_link,
)
from tests.support.pg import one_row

pytestmark = pytest.mark.integration


async def _pool(dsn: str):
    pool = make_pool(dsn)
    await pool.open()
    return pool


def _seed_body(dsn: str, name: str, body_type: str):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return one_row(
            conn.execute(
                "INSERT INTO leadership_bodies (name, body_type) VALUES (%s, %s)"
                " RETURNING id",
                (name, body_type),
            )
        )[0]


async def test_marker_starts_false_and_flips(database) -> None:
    pool = await _pool(database.app_dsn)
    try:
        assert await is_group_provisioning_bootstrapped(pool) is False
        await mark_group_provisioning_bootstrapped(pool)
        assert await is_group_provisioning_bootstrapped(pool) is True
    finally:
        await pool.close()


async def test_address_unique_refuses_second_body(database) -> None:
    # Ralsei's chapter claims an address first; Susie's chapter reaching for
    # the same leader address is refused rather than silently overwriting it.
    ralsei_chapter = _seed_body(database.superuser_dsn, "Ralsei Chapter", "chapter")
    susie_chapter = _seed_body(database.superuser_dsn, "Susie Chapter", "chapter")
    pool = await _pool(database.app_dsn)
    try:
        assert (
            await set_body_link(
                pool,
                ralsei_chapter,
                "ralsei-leaders@example.org",
                "ralsei-editors@example.org",
            )
            is True
        )
        with pytest.raises(LinkTaken):
            await set_body_link(
                pool,
                susie_chapter,
                "ralsei-leaders@example.org",
                "susie-editors@example.org",
            )
    finally:
        await pool.close()


async def test_all_bodies_reports_linkage(database) -> None:
    kris_committee = _seed_body(database.superuser_dsn, "Kris Committee", "committee")
    pool = await _pool(database.app_dsn)
    try:
        await set_body_link(
            pool, kris_committee, "kris-leaders@example.org", "kris-editors@example.org"
        )
        rows = await all_bodies(pool)
    finally:
        await pool.close()
    linked = next(row for row in rows if row.id == kris_committee)
    assert linked.name == "Kris Committee"
    assert linked.body_type == "committee"
    assert linked.leader_google_group_email == "kris-leaders@example.org"
    assert linked.member_google_group_email == "kris-editors@example.org"
