"""What the roster pull stores for each member, against a real engine.

A Solidarity Tech account merge deletes the duplicate id upstream and moves its
email, Discord id, or both onto the survivor. Locally the duplicate's row still
holds the value, so the survivor's upsert collides with it. The pull completes
the merge - folds the duplicate row into the survivor's, keeping its manual
group adds and their attribution, and audits it - but only for a row whose
st_id is gone from the roster. A clash with a member still in the roster is bad
data, not a merge, and is skipped and reported with both rows untouched; so is
a merge on a dry run, or on a pull the lapse fuse distrusts.
"""

from __future__ import annotations

from typing import LiteralString

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.db.roster import LAPSE_FUSE_FLOOR, PullReport, pull_roster
from rosadmin.membership.source import BodyType, Email, Leadership, Member, Standing

pytestmark = pytest.mark.integration

_STEERING = frozenset({Leadership(body_type=BodyType.Committee, name="steering")})


def _member(
    st_id: int,
    email: str,
    discord_id: int | None,
    *,
    standing: Standing = Standing.GoodStanding,
    leads: frozenset[Leadership] = frozenset(),
    alternate_name: str | None = None,
) -> Member:
    return Member(
        st_id=st_id,
        email=Email(email),
        alternate_email=None,
        standing=standing,
        discord_id=discord_id,
        first_name=None,
        last_name=None,
        alternate_name=alternate_name,
        is_chapter_leader=len(leads) > 0,
        leads=leads,
    )


#: Enough good-standing members that, absent alongside a merge duplicate, they
#: make one more absence than the lapse fuse lets a single pull apply.
_CROWD = [
    _member(100 + n, f"darkner{n}@example.com", None) for n in range(LAPSE_FUSE_FLOOR)
]


async def _pull(
    database, roster: list[Member], audit: RecordingAuditSink, *, dry_run: bool = False
) -> PullReport:
    pool = make_pool(database.app_dsn)
    await pool.open()
    try:
        return await pull_roster(pool, roster, audit=audit, dry_run=dry_run)
    finally:
        await pool.close()


def _select(database, query: LiteralString) -> list[tuple]:
    with psycopg.connect(database.superuser_dsn) as conn:
        return conn.execute(query).fetchall()


def _seed_manual_adds(database, adds: list[tuple[int, int]]) -> None:
    """Manually add each `(member st_id, adder st_id)` pair to the one body."""
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        for member_st_id, adder_st_id in adds:
            conn.execute(
                "INSERT INTO body_memberships"
                " (member_id, body_id, role, added_by, manually_added_at)"
                " SELECT m.id, b.id, 'member', adder.id, now()"
                " FROM members m, members adder, leadership_bodies b"
                " WHERE m.st_id = %s AND adder.st_id = %s",
                (member_st_id, adder_st_id),
            )


@pytest.mark.parametrize(
    ("before", "roster", "dry_run", "merges", "skipped", "st_ids"),
    [
        pytest.param(
            [_member(1, "susie@example.com", 555)],
            [_member(2, "susie@example.com", 999)],
            False,
            [(2, 1)],
            [],
            [2],
            id="merge-moves-the-email",
        ),
        pytest.param(
            [_member(1, "susie@example.com", 555)],
            [_member(2, "ralsei@example.com", 555)],
            False,
            [(2, 1)],
            [],
            [2],
            id="merge-moves-the-discord-id",
        ),
        pytest.param(
            [
                _member(1, "susie@example.com", 111),
                _member(2, "noelle@example.com", 555),
            ],
            [_member(3, "susie@example.com", 555)],
            False,
            [(3, 1), (3, 2)],
            [],
            [3],
            id="merge-folds-two-duplicates-into-one",
        ),
        pytest.param(
            [],
            [
                _member(1, "susie@example.com", 555),
                _member(2, "ralsei@example.com", 555),
            ],
            False,
            [],
            [2],
            [1],
            id="clash-between-two-present-members",
        ),
        pytest.param(
            [],
            [
                _member(
                    1,
                    "susie@example.com",
                    555,
                    standing=Standing.Lapsed,
                    leads=_STEERING,
                ),
                _member(2, "susie@example.com", 999),
            ],
            False,
            [],
            [2],
            [1],
            id="clash-with-a-present-lapsed-leader",
        ),
        pytest.param(
            [
                _member(1, "susie@example.com", 111),
                _member(2, "ralsei@example.com", 555),
            ],
            [
                _member(2, "ralsei@example.com", 555),
                _member(3, "susie@example.com", 555),
            ],
            False,
            [],
            [3],
            [1, 2],
            id="duplicate-restored-when-a-present-member-still-clashes",
        ),
        pytest.param(
            [_member(1, "susie@example.com", 555)],
            [_member(2, "susie@example.com", 999)],
            True,
            [],
            [2],
            [1],
            id="dry-run-holds-the-merge",
        ),
        pytest.param(
            [_member(1, "susie@example.com", 555), *_CROWD],
            [_member(2, "susie@example.com", 999)],
            False,
            [],
            [2],
            [1, *(m.st_id for m in _CROWD)],
            id="lapse-fuse-holds-the-merge",
        ),
    ],
)
async def test_pull_merges_only_a_duplicate_gone_from_the_roster(
    database, before, roster, dry_run, merges, skipped, st_ids
) -> None:
    await _pull(database, before, RecordingAuditSink())
    audit = RecordingAuditSink()
    report = await _pull(database, roster, audit, dry_run=dry_run)

    assert (
        sorted((m.survivor_st_id, m.duplicate_st_id) for m in report.merges) == merges
    )
    assert report.skipped_st_ids == skipped
    assert [(r.action, r.subject) for r in audit.records] == [
        ("roster_account_merged", str(m.duplicate_member_id)) for m in report.merges
    ]
    rows = _select(database, "SELECT st_id FROM members ORDER BY st_id")
    assert [st_id for (st_id,) in rows] == st_ids


_SURVIVOR_BEFORE = _member(2, "susie.alt@example.com", 999)


@pytest.mark.parametrize(
    ("survivor_before", "survivor_adder", "adds_after"),
    [
        pytest.param(
            None, None, [(2, 5), (6, 2)], id="survivor-takes-over-the-duplicate-row"
        ),
        pytest.param(
            _SURVIVOR_BEFORE,
            None,
            [(2, 5), (6, 2)],
            id="duplicate-folds-into-the-survivor-row",
        ),
        pytest.param(
            _SURVIVOR_BEFORE,
            6,
            [(2, 6), (6, 2)],
            id="survivor-keeps-its-own-add-to-a-shared-body",
        ),
    ],
)
async def test_merge_keeps_manual_adds_and_their_attribution(
    database, survivor_before, survivor_adder, adds_after
) -> None:
    # Ralsei leads Steering and manually added Susie's duplicate account, which in
    # turn added Kris. Once Solidarity Tech merges the duplicate into Susie's
    # surviving account, Susie is still in Steering on Ralsei's say-so, and Kris's
    # add is credited to Susie rather than to no one.
    ralsei = _member(5, "ralsei@example.com", None, leads=_STEERING)
    kris = _member(6, "kris@example.com", None)
    duplicate = _member(1, "susie@example.com", 555)
    before = [ralsei, kris, duplicate]
    adds = [(1, 5), (6, 1)]
    if survivor_before is not None:
        before.append(survivor_before)
    if survivor_adder is not None:
        adds.append((2, survivor_adder))
    await _pull(database, before, RecordingAuditSink())
    _seed_manual_adds(database, adds)
    ids_before = dict(_select(database, "SELECT st_id, id FROM members"))

    survivor = _member(2, "susie@example.com", 999)
    report = await _pull(database, [ralsei, kris, survivor], RecordingAuditSink())

    assert [(m.survivor_st_id, m.duplicate_st_id) for m in report.merges] == [(2, 1)]
    ids_after = dict(_select(database, "SELECT st_id, id FROM members"))
    assert ids_after[2] == ids_before[1 if survivor_before is None else 2]
    assert (
        _select(
            database,
            "SELECT m.st_id, adder.st_id FROM body_memberships bm"
            " JOIN members m ON m.id = bm.member_id"
            " LEFT JOIN members adder ON adder.id = bm.added_by"
            " WHERE bm.role = 'member' ORDER BY m.st_id",
        )
        == adds_after
    )


async def test_pull_stores_the_chosen_name(database) -> None:
    # A chosen name overrides a possibly-legal first name everywhere a leader
    # sees the member, so the pull must carry it into the row the panel reads.
    await _pull(
        database,
        [_member(1, "kris@example.com", None, alternate_name="Kris")],
        RecordingAuditSink(),
    )
    assert _select(database, "SELECT alternate_name FROM members") == [("Kris",)]
