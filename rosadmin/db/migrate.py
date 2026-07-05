"""Applying the schema migrations - shared by the deploy `migrate` command and
the integration test rig, so both build the yoyo URI and locate the migrations
one way.

On the box `rosadmin migrate` runs this as an `ExecStartPre` phase before the
service serves: it connects as the migration role over TCP loopback with scram
(the runtime role has no schema rights and reaches the database only by peer over
the socket), applies any pending migrations, and a failure aborts the start
rather than serving a half-migrated schema.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from urllib.parse import quote

from yoyo import get_backend, read_migrations

from rosadmin.credentials import read_credential


def migrations_path() -> str:
    """The packaged `rosadmin/migrations` directory.

    Located by package rather than working directory so it resolves from a
    relocated `uv` venv on the box, where the process's working directory is the
    release tree, not the source checkout the tests run from.
    """
    return str(importlib.resources.files("rosadmin") / "migrations")


def migrate_uri(*, user: str, password: str, host: str, port: str, dbname: str) -> str:
    """A yoyo URI for the psycopg 3 backend.

    yoyo picks its backend from the URI scheme, so `postgresql+psycopg` selects
    psycopg 3 - it needs a URI, not the keyword/value DSN psycopg itself accepts.
    User and password are percent-encoded so a credential carrying URI
    metacharacters cannot corrupt the URI.
    """
    return (
        f"postgresql+psycopg://{quote(user)}:{quote(password)}@{host}:{port}/{dbname}"
    )


def apply_pending(uri: str) -> None:
    """Apply every not-yet-applied migration, under yoyo's advisory lock."""
    backend = get_backend(uri)
    migrations = read_migrations(migrations_path())
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))


def _migrate_password(env: Mapping[str, str]) -> str:
    """The migration role's scram password: a systemd credential on the box, an
    env var in dev.

    Read through the shared [`read_credential`] from
    `$CREDENTIALS_DIRECTORY/db_migration_password` or
    `ROSADMIN_DB_MIGRATE_PASSWORD`. Stripping there keeps a credential file's
    trailing newline from diverging from the stored scram verifier. The password
    is never logged.
    """
    password = read_credential(
        env, "db_migration_password", "ROSADMIN_DB_MIGRATE_PASSWORD"
    )
    if password is None:
        raise RuntimeError("database migration password is not configured")
    return password


def migrate_uri_from_env(env: Mapping[str, str]) -> str:
    """Build the deploy migration URI from the environment and credential store.

    Mirrors `dsn_from_env`'s split but targets the migration role over TCP
    loopback rather than the runtime role over the socket: the
    `ROSADMIN_DB_MIGRATE_*` parts default to
    `rosadmin_staging_migrate@127.0.0.1:5433/rosadmin_staging`.
    """
    return migrate_uri(
        user=env.get("ROSADMIN_DB_MIGRATE_USER", "rosadmin_staging_migrate"),
        password=_migrate_password(env),
        host=env.get("ROSADMIN_DB_MIGRATE_HOST", "127.0.0.1"),
        port=env.get("ROSADMIN_DB_MIGRATE_PORT", "5433"),
        dbname=env.get("ROSADMIN_DB_NAME", "rosadmin_staging"),
    )
