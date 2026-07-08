"""The shared pytest/behave Postgres rig.

An ephemeral `postgres:18` container - the same engine as the box - with the
schema and least-privilege grants applied through the real yoyo migrations, and
the runtime app role joined to the group role those grants target. pytest's
`database` fixture and behave's `@db`-tagged scenarios both build on `start`
and `truncate`, so the two runners share one rig instead of drifting copies.
Tests connect as the container superuser to seed or inspect rows, or as the
app role to prove the grants.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

import psycopg
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


@dataclass(frozen=True)
class Db:
    """Two DSNs into the same ephemeral database: full-power and app-scoped."""

    superuser_dsn: str
    app_dsn: str


def _dsn(container: PostgresContainer, *, user: str, password: str, dbname: str) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


_CONNECT_ATTEMPTS = 30
_CONNECT_DELAY = 1.0  # seconds between attempts
_CONNECT_TIMEOUT = 5  # libpq connect_timeout, seconds per attempt


def _connect(dsn: str):
    """Connect (autocommit) to the container, retrying a transient failure.

    testcontainers waits for the container to report ready, but on Podman over
    WSL the host-side port forward is occasionally not accepting connections for
    a beat after that, so a fresh connect times out. A bounded retry rides over
    the blip, and the per-attempt `connect_timeout` turns a wedged connect into a
    fast, retryable error rather than an indefinite hang that ignores Ctrl+C.
    """
    last: psycopg.OperationalError | None = None
    for _ in range(_CONNECT_ATTEMPTS):
        try:
            return psycopg.connect(
                dsn, autocommit=True, connect_timeout=_CONNECT_TIMEOUT
            )
        except psycopg.OperationalError as error:
            last = error
            time.sleep(_CONNECT_DELAY)
    raise RuntimeError(
        f"could not connect to the test database after {_CONNECT_ATTEMPTS} attempts"
    ) from last


def _ensure_docker_host() -> None:
    """Point testcontainers at the Podman pipe on Windows when `DOCKER_HOST` is unset.

    Without a Docker Desktop install, docker-py defaults to its own named pipe
    (`//./pipe/docker_engine`), which does not exist here, so
    `PostgresContainer.start()` hangs forever on the connection instead of
    failing. When `DOCKER_HOST` is unset on Windows, derive it from the running
    Podman machine, so a bare `uv run behave` or `pytest -m integration` works
    without the operator remembering the prefix. An explicit `DOCKER_HOST`
    always wins; on Linux (the dev laptop and CI) the daemon socket is reached
    directly, so this is a no-op there.
    """
    if os.environ.get("DOCKER_HOST") or sys.platform != "win32":
        return
    try:
        out = subprocess.run(
            ["podman", "machine", "inspect"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
        machine = json.loads(out)[0]
        pipe = machine["ConnectionInfo"]["PodmanPipe"]["Path"]
    except (OSError, subprocess.SubprocessError, ValueError, LookupError, TypeError):
        return  # no podman, or an unexpected shape - let testcontainers surface it
    if machine.get("State") != "running":
        raise RuntimeError(
            "the Podman machine is not running; start it with `podman machine start`"
        )
    # `\\.\pipe\name` -> `npipe:////./pipe/name`, the URL docker-py expects.
    os.environ["DOCKER_HOST"] = "npipe://" + pipe.replace("\\", "/")


def start() -> tuple[PostgresContainer, Db]:
    """Start the container, provision the roles, apply migrations.

    Returns the running container alongside its `Db` DSNs, so the caller
    controls the container's lifetime - a pytest fixture's try/finally, or
    behave's manual stop in `after_all`.
    """
    _ensure_docker_host()
    container = PostgresContainer("postgres:18", driver=None)
    container.start()
    superuser = _dsn(
        container,
        user=container.username,
        password=container.password,
        dbname=container.dbname,
    )
    with _connect(superuser) as conn:
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
    db = Db(
        superuser_dsn=superuser,
        app_dsn=_dsn(
            container, user=_APP_ROLE, password=_APP_PASSWORD, dbname=container.dbname
        ),
    )
    return container, db


def truncate(db: Db) -> None:
    """Empty every domain table, restarting identities, between isolated tests."""
    statement = "TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE"
    with _connect(db.superuser_dsn) as conn:
        conn.execute(statement)


def one_row[R](cursor: psycopg.Cursor[R]) -> R:
    """The single row a RETURNING or aggregate query must yield.

    `fetchone` is Optional by type; a seed or count query that yields nothing
    is a broken test, so this narrows and fails loudly in one place instead of
    each call site re-asserting.
    """
    row = cursor.fetchone()
    assert row is not None
    return row
