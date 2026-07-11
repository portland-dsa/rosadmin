"""The sync command: one pull-then-reconcile run, the unit the timer fires."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from cyclopts import App, Parameter

from rosadmin.db import dsn_from_env, make_pool
from rosadmin.db.audit import PostgresAuditSink, audit_key_from_env
from rosadmin.group_sync import (
    DryRunGroupSync,
    GroupSync,
    RecordingProvisioner,
    group_lister_from_env,
    group_sync_from_env,
    provisioner_from_env,
)
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.source import ANOMALY_WARNING, Email
from rosadmin.reconcile import (
    ProvisionConfig,
    RosterPullUnsafe,
    SweepAlreadyRunning,
    SweepReport,
    run_sweep,
)

logger = logging.getLogger(__name__)

sync_app = App(name="sync", help="Reconcile Google Groups to the membership records.")


def _main_group_from_env() -> Email:
    raw = os.environ.get("ROSADMIN_MAIN_GROUP_EMAIL")
    if raw is None or len(raw) == 0:
        raise RuntimeError(
            "ROSADMIN_MAIN_GROUP_EMAIL is required: the sweep needs the "
            "org-wide group the whole good-standing roster syncs into"
        )
    return Email(raw)


def _provision_config_from_env() -> ProvisionConfig:
    domain = os.environ.get("ROSADMIN_GROUP_EMAIL_DOMAIN")
    if not domain:
        raise RuntimeError(
            "ROSADMIN_GROUP_EMAIL_DOMAIN is required: the domain every minted "
            "group address takes"
        )
    name = os.environ.get("ROSADMIN_MAIN_GROUP_NAME")
    if not name:
        raise RuntimeError(
            "ROSADMIN_MAIN_GROUP_NAME is required: the main group's display name"
        )
    cap = int(os.environ.get("ROSADMIN_MASS_CREATION_TRIPWIRE", "10"))
    return ProvisionConfig(
        domain=domain, main_group_name=name, mass_creation_tripwire=cap
    )


@sync_app.command(name="run")
async def sync_run(
    dry_run: Annotated[
        bool,
        Parameter(
            help="Rehearse the Google sync: real reads, the real diff, no Google "
            "writes. The roster pull still runs and writes the database - add "
            "--skip-pull to reconcile against the database as it stands."
        ),
    ] = False,
    skip_pull: Annotated[
        bool,
        Parameter(help="Reconcile against the database as it stands, without pulling."),
    ] = False,
    allow_mass_removal: Annotated[
        bool,
        Parameter(help="Override the mass-removal fuse for a deliberate purge."),
    ] = False,
) -> None:
    """Pull the roster, then converge every linked group and the main group.

    Exits nonzero when another sweep holds the lock, when any Google
    operation failed, or when the mass-removal fuse refused a group's
    removals - a failed run is a failed unit in the journal.
    """
    main_group = _main_group_from_env()
    source = None if skip_pull else SolidarityTechClient.from_env(os.environ)
    lister = group_lister_from_env(os.environ)
    expect_example = os.environ.get("ROSADMIN_EXPECT_EXAMPLE_EMAILS") == "1"
    sync: GroupSync = (
        DryRunGroupSync(expect_example_emails=expect_example)
        if dry_run
        else group_sync_from_env(os.environ)
    )
    base_provisioner = provisioner_from_env(os.environ)
    provisioner = (
        RecordingProvisioner(base_provisioner)
        if dry_run and base_provisioner is not None
        else base_provisioner
    )
    provision = _provision_config_from_env()
    pool = make_pool(dsn_from_env(os.environ))
    await pool.open()
    try:
        audit = PostgresAuditSink(pool, audit_key_from_env(os.environ))
        report = await run_sweep(
            pool,
            source=source,
            lister=lister,
            sync=sync,
            audit=audit,
            main_group_email=main_group,
            provisioner=provisioner,
            provision=provision,
            allow_mass_removal=allow_mass_removal,
            dry_run=dry_run,
        )
    except SweepAlreadyRunning:
        raise SystemExit("another sweep run holds the lock; exiting")
    except RosterPullUnsafe as unsafe:
        raise SystemExit(str(unsafe))
    finally:
        await pool.close()
        if source is not None:
            await source.aclose()
    _report(report, dry_run=dry_run)
    if report.has_failures:
        raise SystemExit(1)


def _report(report: SweepReport, *, dry_run: bool) -> None:
    if report.pull is not None:
        for anomaly in report.pull.anomalies:
            logger.warning(ANOMALY_WARNING, anomaly.member_id, anomaly.assessment.value)
        logger.info(
            "pull: %d members, %d absent lapsed, %d skipped",
            report.pull.members_upserted,
            report.pull.absent_lapsed,
            len(report.pull.skipped_st_ids),
        )
    if report.provision is not None:
        p = report.provision
        logger.info(
            "provision: %d created, %d adopted, %d already linked, %d diverged, "
            "%d refused over cap, %d failed",
            p.created,
            p.adopted,
            p.already_linked,
            p.diverged,
            p.refused_over_cap,
            p.failed,
        )
    mode = "dry-run" if dry_run else "applied"
    for g in report.groups:
        logger.info(
            "%s %s: planned +%d/-%d, applied %d, converged %d, "
            "skipped %d, refused %d, failed %d",
            mode,
            g.group_email,
            g.planned_adds,
            g.planned_removes,
            g.applied,
            g.already_converged,
            g.skipped,
            g.refused,
            g.failed,
        )
