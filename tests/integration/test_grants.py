"""The audit log is append-only for the app role: INSERT yes, read/mutate no.

Proves the enforceable half of that contract against a real engine, which a
whole-pipeline behavior test cannot reach.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.support.pg import one_row

pytestmark = pytest.mark.integration


def test_app_role_may_insert_audit_but_not_read_or_mutate(database) -> None:
    with psycopg.connect(database.app_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO audit_log (actor_hmac, action) VALUES (%s, %s)",
            ("deadbeef", "login"),
        )
        for statement in (
            "SELECT * FROM audit_log",
            "UPDATE audit_log SET action = 'x'",
            "DELETE FROM audit_log",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_app_role_has_full_access_to_data_tables(database) -> None:
    with psycopg.connect(database.app_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s)",
            (1, "ralsei@example.com", "good_standing"),
        )
        rows = conn.execute("SELECT email FROM members").fetchall()
        assert ("ralsei@example.com",) in rows


def test_app_role_may_record_an_unmirrorable_address_but_not_erase_it(
    database,
) -> None:
    with psycopg.connect(database.app_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO unmirrorable_addresses (address, reason) VALUES (%s, %s)",
            ("spamton@example.com", "no_google_account"),
        )
        conn.execute(
            "UPDATE unmirrorable_addresses SET observed_at = now() WHERE address = %s",
            ("spamton@example.com",),
        )
        rows = conn.execute("SELECT address FROM unmirrorable_addresses").fetchall()
        assert ("spamton@example.com",) in rows
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM unmirrorable_addresses")


def test_app_role_may_link_a_body_but_not_rename_it(database) -> None:
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        body_id = one_row(
            conn.execute(
                "INSERT INTO leadership_bodies (name, body_type) VALUES (%s, %s)"
                " RETURNING id",
                ("card castle", "chapter"),
            )
        )[0]
    with psycopg.connect(database.app_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE leadership_bodies"
            " SET leader_google_group_email = %s, member_google_group_email = %s"
            " WHERE id = %s",
            ("leaders@example.com", "members@example.com", body_id),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE leadership_bodies SET name = %s WHERE id = %s",
                ("dark castle", body_id),
            )
