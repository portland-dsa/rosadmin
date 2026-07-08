"""The roster pull command: read Solidarity Tech and materialize it into Postgres."""

from __future__ import annotations

import logging
import os

from cyclopts import App
from psycopg_pool import AsyncConnectionPool

from rosadmin.db import dsn_from_env, make_pool
from rosadmin.db.roster import PullReport, pull_roster
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.source import ANOMALY_WARNING, MembershipSource

logger = logging.getLogger(__name__)

roster_app = App(
    name="roster", help="Materialize Solidarity Tech membership into Postgres."
)


async def run_pull(pool: AsyncConnectionPool, source: MembershipSource) -> PullReport:
    """Read `source`'s whole roster and materialize it into `pool`.

    The composition the CLI and the admin socket's pull trigger share; callers
    own `source` and `pool`'s lifecycles (opening/closing the pool, closing the
    source's client) on their own terms, since the CLI's are dedicated to one
    run while the admin route reuses the service's already-open pool.
    """
    members = await source.list_members()
    return await pull_roster(pool, members)


@roster_app.command(name="pull")
async def roster_pull() -> None:
    """Pull the whole Solidarity Tech roster into the members and leadership tables.

    Warns, once per member, on every assessment the pull flags as an anomaly -
    the raw chapter-leader flag and the derived leadership roles disagree. The
    warning names only the internal member id, never an email or a name.
    """
    source = SolidarityTechClient.from_env(os.environ)
    pool = make_pool(dsn_from_env(os.environ))
    await pool.open()
    try:
        report = await run_pull(pool, source)
    finally:
        await pool.close()
        await source.aclose()

    for anomaly in report.anomalies:
        logger.warning(ANOMALY_WARNING, anomaly.member_id, anomaly.assessment.value)

    if report.skipped_st_ids:
        logger.warning(
            "skipped %d member(s) on a unique-constraint clash (Solidarity Tech ids %s)",
            len(report.skipped_st_ids),
            report.skipped_st_ids,
        )

    logger.info(
        "roster pull complete: %d members, %d bodies, %d leader rows, "
        "%d anomalies, %d skipped",
        report.members_upserted,
        report.bodies_upserted,
        report.leader_rows,
        len(report.anomalies),
        len(report.skipped_st_ids),
    )
