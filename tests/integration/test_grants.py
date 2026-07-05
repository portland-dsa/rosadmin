"""The audit log is append-only for the app role: INSERT yes, read/mutate no.

Proves the enforceable half of that contract against a real engine, which a
whole-pipeline behavior test cannot reach.
"""

from __future__ import annotations

import psycopg
import pytest

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
