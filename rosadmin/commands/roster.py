"""The roster pull command: read Solidarity Tech and materialize it into Postgres."""

from __future__ import annotations

import logging
import os

from cyclopts import App
from psycopg_pool import AsyncConnectionPool

from rosadmin.db import dsn_from_env, make_pool
from rosadmin.db.audit import AuditSink, PostgresAuditSink, audit_key_from_env
from rosadmin.db.roster import PullReport, pull_roster
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.source import ANOMALY_WARNING, MembershipSource

logger = logging.getLogger(__name__)

roster_app = App(
    name="roster", help="Materialize Solidarity Tech membership into Postgres."
)


async def run_pull(
    pool: AsyncConnectionPool, source: MembershipSource, audit: AuditSink
) -> PullReport:
    """Read `source`'s whole roster and materialize it into `pool`.

    The composition the CLI and the admin socket's pull trigger share; callers
    own `source` and `pool`'s lifecycles (opening/closing the pool, closing the
    source's client) on their own terms, since the CLI's are dedicated to one
    run while the admin route reuses the service's already-open pool.
    """
    members = await source.list_members()
    return await pull_roster(pool, members, audit=audit)


def warn_pull_findings(report: PullReport) -> None:
    """Warn, by id, on every member of a pull an operator may need to chase.

    Anomalies name only the internal member id; skipped members and account
    merges name Solidarity Tech ids, never an email or a name. Shared
    by `roster pull` and the `sync run` sweep, so the timer's journal carries
    the same ids a manual pull would.
    """
    for anomaly in report.anomalies:
        logger.warning(ANOMALY_WARNING, anomaly.member_id, anomaly.assessment.value)
    if len(report.skipped_st_ids) > 0:
        logger.warning(
            "skipped %d member(s) on a unique-constraint clash (Solidarity Tech ids %s)",
            len(report.skipped_st_ids),
            report.skipped_st_ids,
        )
    for merge in report.merges:
        logger.warning(
            "merged Solidarity Tech id %d into Solidarity Tech id %d, which "
            "Solidarity Tech gave its email or Discord id; its manual group adds "
            "carried over",
            merge.duplicate_st_id,
            merge.survivor_st_id,
        )


@roster_app.command(name="pull")
async def roster_pull() -> None:
    """Pull the whole Solidarity Tech roster into the members and leadership tables.

    Warns, once per member, on every assessment the pull flags as an anomaly
    (the raw chapter-leader flag and the derived leadership roles disagree), on
    every member skipped for a clash, and on every account merge it completes.
    """
    source = SolidarityTechClient.from_env(os.environ)
    pool = make_pool(dsn_from_env(os.environ))
    await pool.open()
    try:
        audit = PostgresAuditSink(pool, audit_key_from_env(os.environ))
        report = await run_pull(pool, source, audit)
    finally:
        await pool.close()
        await source.aclose()

    warn_pull_findings(report)
    logger.info(
        "roster pull complete: %d members, %d bodies, %d leader rows, %d anomalies, "
        "%d skipped, %d account merges, %d absent members lapsed, "
        "%d lapse refused",
        report.members_upserted,
        report.bodies_upserted,
        report.leader_rows,
        len(report.anomalies),
        len(report.skipped_st_ids),
        len(report.merges),
        report.absent_lapsed,
        report.lapse_refused,
    )

    if report.lapse_refused > 0:
        logger.error(
            "roster pull refused to lapse %d member(s): an implausible mass "
            "absence, not applied",
            report.lapse_refused,
        )
        raise SystemExit(1)
