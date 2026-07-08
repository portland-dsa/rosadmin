"""The two CHECK constraints the mutation columns add, against a real engine.

No app code path can produce a half-linked body or an attributed-but-undated
manual add - the write endpoints lean on the constraint
instead of re-deriving the invariant themselves - so the only way to prove the
refusal holds is a direct illegal INSERT.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from tests.support.pg import one_row

pytestmark = pytest.mark.integration


@pytest.fixture
def seed(database):
    """A member, a second member to attribute adds to, and an unlinked body."""
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        member_id = one_row(
            conn.execute(
                "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s)"
                " RETURNING id",
                (1, "susie@example.com", "good_standing"),
            )
        )[0]
        adder_id = one_row(
            conn.execute(
                "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s)"
                " RETURNING id",
                (2, "ralsei@example.com", "good_standing"),
            )
        )[0]
        body_id = one_row(
            conn.execute(
                "INSERT INTO leadership_bodies (name, body_type) VALUES (%s, %s)"
                " RETURNING id",
                ("card castle", "chapter"),
            )
        )[0]
    return member_id, adder_id, body_id


@pytest.mark.parametrize(
    "kind, columns, should_raise",
    [
        pytest.param(
            "linked_pair",
            {
                "leader_google_group_email": "leaders@example.com",
                "member_google_group_email": None,
            },
            True,
            id="leader-only-is-half-linked",
        ),
        pytest.param(
            "linked_pair",
            {
                "leader_google_group_email": None,
                "member_google_group_email": "members@example.com",
            },
            True,
            id="member-only-is-half-linked",
        ),
        pytest.param(
            "linked_pair",
            {
                "leader_google_group_email": "leaders@example.com",
                "member_google_group_email": "members@example.com",
            },
            False,
            id="both-set-is-linked",
        ),
        pytest.param(
            "linked_pair",
            {"leader_google_group_email": None, "member_google_group_email": None},
            False,
            id="both-null-is-unlinked",
        ),
        pytest.param(
            "manual_provenance",
            {"added_by": True, "manually_added_at": False},
            True,
            id="attributed-but-undated",
        ),
        pytest.param(
            "manual_provenance",
            {"added_by": False, "manually_added_at": True},
            False,
            id="dated-without-attribution",
        ),
        pytest.param(
            "manual_provenance",
            {"added_by": True, "manually_added_at": True},
            False,
            id="dated-and-attributed",
        ),
        pytest.param(
            "manual_provenance",
            {"added_by": False, "manually_added_at": False},
            False,
            id="neither-is-not-a-manual-add",
        ),
    ],
)
def test_check_constraints_refuse_half_states(
    database, seed, kind, columns, should_raise
):
    member_id, adder_id, body_id = seed

    def attempt(conn: psycopg.Connection) -> None:
        if kind == "linked_pair":
            conn.execute(
                "UPDATE leadership_bodies"
                " SET leader_google_group_email = %s, member_google_group_email = %s"
                " WHERE id = %s",
                (
                    columns["leader_google_group_email"],
                    columns["member_google_group_email"],
                    body_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO body_memberships"
                " (member_id, body_id, role, added_by, manually_added_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    member_id,
                    body_id,
                    "member",
                    adder_id if columns["added_by"] else None,
                    datetime.now(UTC) if columns["manually_added_at"] else None,
                ),
            )

    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        if should_raise:
            with pytest.raises(psycopg.errors.CheckViolation):
                attempt(conn)
        else:
            attempt(conn)
