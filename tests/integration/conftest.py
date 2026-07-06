"""Ephemeral real Postgres for the DB-layer tests.

A session-scoped Postgres 18 container - the same engine as the box - with the
schema and the least-privilege grants both applied through the real yoyo
migrations, and the runtime app role joined to the group role those grants
target. Tests connect as the container superuser to seed rows, or as the app role
to prove the grants. An autouse truncation keeps each test isolated while the
container is reused for speed.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql
from testcontainers.postgres import PostgresContainer
from yoyo import get_backend, read_migrations

from rosadmin.db.migrate import migrate_uri, migrations_path

_APP_GROUP = "rosadmin_app"
_APP_ROLE = "rosadmin_staging_app"
_APP_PASSWORD = "apptest"
_TABLES = (
    "members",
    "leadership_bodies",
    "body_memberships",
    "sessions",
    "jti_replay",
    "rate_limit_counters",
    "audit_log",
)


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    # psycopg's async pool cannot run under Windows' default ProactorEventLoop;
    # it needs a selector loop. Linux (dev laptop and CI alike) keeps the
    # default policy untouched.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@dataclass(frozen=True)
class Db:
    """Two DSNs into the same ephemeral database: full-power and app-scoped."""

    superuser_dsn: str
    app_dsn: str


def _dsn(container: PostgresContainer, *, user: str, password: str, dbname: str) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


@pytest.fixture(scope="session")
def database() -> Iterator[Db]:
    with PostgresContainer("postgres:18", driver=None) as container:
        superuser = _dsn(
            container,
            user=container.username,
            password=container.password,
            dbname=container.dbname,
        )
        with psycopg.connect(superuser, autocommit=True) as conn:
            # The grant migration grants to the group role that each stage's login
            # role joins, so recreate that shape - group role plus a member login
            # role - and the app-role grant tests then exercise the real
            # inheritance path. PASSWORD is a literal in Postgres's own grammar,
            # not a value position, so sql.Literal renders it rather than a bound
            # parameter.
            conn.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(_APP_GROUP))
            )
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                    sql.Identifier(_APP_ROLE),
                    sql.Literal(_APP_PASSWORD),
                    sql.Identifier(_APP_GROUP),
                )
            )
        backend = get_backend(
            migrate_uri(
                user=container.username,
                password=container.password,
                host=container.get_container_host_ip(),
                port=str(container.get_exposed_port(5432)),
                dbname=container.dbname,
            )
        )
        migrations = read_migrations(migrations_path())
        with backend.lock():
            backend.apply_migrations(backend.to_apply(migrations))
        yield Db(
            superuser_dsn=superuser,
            app_dsn=_dsn(
                container,
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                dbname=container.dbname,
            ),
        )


@pytest.fixture(autouse=True)
def _clean(database) -> None:
    statement = "TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE"
    with psycopg.connect(database.superuser_dsn, autocommit=True) as conn:
        conn.execute(statement)
