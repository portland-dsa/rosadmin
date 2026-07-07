"""The database layer: one async pool, name-based row mapping, typed access.

Every transient backend piece lives in Postgres - sessions, replay cache,
rate limits, audit - so this package is the single door to it. The pool opens at
service startup and closes at shutdown; the leaf modules (`sessions`, `audit`)
hold the queries. The pool's per-connection `configure` registers the
`member_standing` and `leadership_assessment` enums so each round-trips as its
existing `Standing` / `LeadershipAssessment` type rather than a bare string.
"""

from __future__ import annotations

from collections.abc import Mapping

from psycopg import AsyncConnection
from psycopg.types.enum import EnumInfo, register_enum
from psycopg_pool import AsyncConnectionPool

from rosadmin.membership.source import LeadershipAssessment, Standing

_STANDING_LABELS = [
    (Standing.GoodStanding, "good_standing"),
    (Standing.Lapsed, "lapsed"),
]

_LEADERSHIP_ASSESSMENT_LABELS = [
    (LeadershipAssessment.Leader, "leader"),
    (LeadershipAssessment.NonLeader, "non_leader"),
    (LeadershipAssessment.UnmarkedLeader, "unmarked_leader"),
    (LeadershipAssessment.EmptyLeader, "empty_leader"),
]


def dsn_from_env(env: Mapping[str, str]) -> str:
    """Build the libpq DSN. `ROSADMIN_DB_DSN` wins; otherwise assemble from parts.

    On the box the app connects over the Unix socket by peer authentication, so
    the assembled DSN carries the socket directory, port, and database name with
    no password (`host=/var/run/postgresql port=5433 dbname=rosadmin_staging
    user=rosadmin_staging_app`). The port is load-bearing: rosadmin runs its own
    cluster on 5433, and over a socket libpq derives the socket file from the
    port, so a portless DSN would land on the neighboring 5432 cluster instead.
    Tests and local dev set `ROSADMIN_DB_DSN` to a TCP DSN with a password.
    """
    explicit = env.get("ROSADMIN_DB_DSN")
    if explicit:
        return explicit
    parts = {
        "host": env.get("ROSADMIN_DB_HOST", "/var/run/postgresql"),
        "port": env.get("ROSADMIN_DB_PORT", "5433"),
        "dbname": env.get("ROSADMIN_DB_NAME", "rosadmin_staging"),
        "user": env.get("ROSADMIN_DB_USER", "rosadmin_staging_app"),
    }
    return " ".join(f"{key}={value}" for key, value in parts.items())


async def _configure(conn: AsyncConnection) -> None:
    info = await EnumInfo.fetch(conn, "member_standing")
    if info is not None:
        register_enum(info, conn, Standing, mapping=_STANDING_LABELS)
    info = await EnumInfo.fetch(conn, "leadership_assessment")
    if info is not None:
        register_enum(
            info, conn, LeadershipAssessment, mapping=_LEADERSHIP_ASSESSMENT_LABELS
        )


def make_pool(dsn: str) -> AsyncConnectionPool:
    """An unopened async pool that registers the `member_standing` and
    `leadership_assessment` enums per connection."""
    return AsyncConnectionPool(dsn, open=False, configure=_configure)
