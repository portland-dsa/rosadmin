from __future__ import annotations

import pytest

from rosadmin.db import dsn_from_env
from rosadmin.db.migrate import migrate_uri_from_env


def test_dsn_from_env_defaults_to_the_5433_cluster():
    # The port is load-bearing: over the socket libpq derives the socket file from
    # it, so a portless DSN would land on the neighboring 5432 cluster.
    dsn = dsn_from_env({})
    assert "port=5433" in dsn
    assert "host=/var/run/postgresql" in dsn
    assert "dbname=rosadmin_staging" in dsn
    assert "user=rosadmin_staging_app" in dsn


def test_dsn_from_env_explicit_dsn_wins():
    explicit = "host=localhost port=55432 dbname=x user=y password=z"
    assert dsn_from_env({"ROSADMIN_DB_DSN": explicit}) == explicit


def test_migrate_uri_defaults_target_the_migrate_role_on_5433():
    uri = migrate_uri_from_env({"ROSADMIN_DB_MIGRATE_PASSWORD": "pw"})
    assert uri == (
        "postgresql+psycopg://rosadmin_staging_migrate:pw@127.0.0.1:5433/rosadmin_staging"
    )


def test_migrate_password_prefers_the_credential_and_strips_newline(tmp_path):
    # systemd delivers the credential as a file that may carry a trailing newline;
    # a stray newline in a scram password would never match the stored verifier.
    (tmp_path / "db_migration_password").write_text("pw\n", encoding="utf-8")
    uri = migrate_uri_from_env({"CREDENTIALS_DIRECTORY": str(tmp_path)})
    assert uri.startswith("postgresql+psycopg://rosadmin_staging_migrate:pw@")


def test_migrate_password_missing_is_an_error():
    with pytest.raises(RuntimeError):
        migrate_uri_from_env({})
