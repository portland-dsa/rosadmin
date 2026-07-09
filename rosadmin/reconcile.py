"""The reconcile sweep's pure core: desired versus actual, per group.

`plan_group` is a pure function from one group's desired set and its actual
remote member list to the adds and removes that converge them. The two
safety rules live here, in testable logic rather than in the apply loop:
only plain USER members are ever removable (owners, managers, and nested
groups survive every sweep), and a mass removal trips a fuse instead of
executing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from googleapiclient.errors import HttpError

from rosadmin.db.audit import AuditSink, record_best_effort
from rosadmin.db.reconcile import desired_audiences, desired_for_group
from rosadmin.db.roster import PullReport, pull_roster
from rosadmin.google_group import GroupMemberEntry, GroupsPermissionLevel
from rosadmin.group_sync import GroupLister, GroupSync, SyncOutcome
from rosadmin.membership.source import Email, MembershipSource

logger = logging.getLogger(__name__)

#: A removal set no larger than this never trips the fuse, however small
#: the group - a 4-member working group must be able to lose a member.
REMOVAL_FUSE_FLOOR = 5

#: Above the floor, removals exceeding this fraction of the group's actual
#: membership trip the fuse. A poisoned desired state - a partial upstream
#: roster, a mock wired where it should not be - computes "remove almost
#: everyone", and the fuse exists to make that a loud refusal instead of an
#: obedient purge.
REMOVAL_FUSE_FRACTION = 0.10


@dataclass(frozen=True)
class PlannedAdd:
    """One address to add, with the member it belongs to (for the audit row)."""

    address: Email
    member_id: UUID


@dataclass(frozen=True)
class GroupPlan:
    """What one group needs to converge: adds, removes, or a tripped fuse."""

    group_email: Email
    adds: tuple[PlannedAdd, ...]
    removes: tuple[Email, ...]
    #: How many removals the fuse refused. Zero means it did not trip; the
    #: refused removals are absent from `removes` entirely.
    refused_removes: int

    @property
    def fuse_tripped(self) -> bool:
        return self.refused_removes > 0


def _removal_budget(actual_size: int) -> int:
    return max(REMOVAL_FUSE_FLOOR, math.ceil(actual_size * REMOVAL_FUSE_FRACTION))


def plan_group(
    group_email: Email,
    desired: dict[str, UUID],
    actual: list[GroupMemberEntry],
    *,
    allow_mass_removal: bool,
) -> GroupPlan:
    """Diff one group. `desired` keys are casefolded addresses.

    Adds compare against every actual entry regardless of role or type - a
    member already present as a MANAGER must not be re-added as a MEMBER.
    Removes draw only from plain USER MEMBER entries, so owners, managers,
    and nested groups are structurally untouchable. Addresses compare
    casefolded because Google reports case it does not enforce.
    """
    present = {entry.email.casefold() for entry in actual}
    adds = tuple(
        PlannedAdd(address=Email(address), member_id=member_id)
        for address, member_id in sorted(desired.items())
        if address not in present
    )
    removable = [
        entry
        for entry in actual
        if entry.permission_level is GroupsPermissionLevel.Member
        and entry.type == "USER"
        and entry.email.casefold() not in desired
    ]
    removes = tuple(Email(entry.email) for entry in removable)
    if allow_mass_removal or len(removes) <= _removal_budget(len(actual)):
        return GroupPlan(
            group_email=group_email, adds=adds, removes=removes, refused_removes=0
        )
    return GroupPlan(
        group_email=group_email,
        adds=adds,
        removes=(),
        refused_removes=len(removes),
    )


#: Serializes sweep runs and nothing else. Session-level, not
#: transaction-level: the Google apply phase runs outside any transaction,
#: so the lock must outlive them all; a killed process releases it with its
#: connection. Distinct from the pull's transaction-scoped lock key.
_SWEEP_LOCK_KEY = 0x_53_57_50_31


class SweepAlreadyRunning(Exception):
    """Another sweep holds the advisory lock; this run did nothing."""


class RosterPullUnsafe(Exception):
    """The pull refused to lapse an implausible number of members, so its
    result is not trustworthy enough to reconcile against."""

    def __init__(self, lapse_refused: int) -> None:
        super().__init__(
            f"roster pull refused to lapse {lapse_refused} members; not reconciling"
        )
        self.lapse_refused = lapse_refused


@dataclass(frozen=True)
class GroupOutcome:
    """One group's slice of a sweep report. Counts only - never addresses."""

    group_email: Email
    planned_adds: int
    planned_removes: int
    applied: int
    already_converged: int
    skipped: int
    refused: int
    failed: int


@dataclass(frozen=True)
class SweepReport:
    """One run's outcome: the pull it began with and every group it touched."""

    pull: PullReport | None
    groups: tuple[GroupOutcome, ...]
    #: False when the environment forbade Google reads entirely - the run
    #: reported desired state and applied nothing.
    lister_available: bool

    @property
    def has_failures(self) -> bool:
        return any(g.failed > 0 or g.refused > 0 for g in self.groups)


async def run_sweep(
    pool: AsyncConnectionPool,
    *,
    source: MembershipSource | None,
    lister: GroupLister | None,
    sync: GroupSync,
    audit: AuditSink,
    main_group_email: Email,
    allow_mass_removal: bool = False,
) -> SweepReport:
    """One full reconcile run. See the module docstring for the shape.

    Raises `SweepAlreadyRunning` when the advisory lock is held. Any pull
    failure propagates before a single Google call is made - reconciling
    against last-good data would be safe, but a failing pull is a signal to
    stop and be seen.
    """
    # The advisory lock rides a dedicated autocommit connection, not a pooled
    # one. On a pooled (non-autocommit) connection the acquiring SELECT opens a
    # transaction that would sit idle-in-transaction across the whole Google
    # phase, which the runtime role's idle-in-transaction timeout eventually
    # kills - ending the session and releasing the lock mid-sweep. An autocommit
    # connection holds no open transaction, so the session and its lock outlive
    # the network phase; closing it in the finally releases the lock.
    conninfo = pool.conninfo
    assert isinstance(
        conninfo, str
    )  # make_pool always builds the pool from a DSN string
    lock_conn = await psycopg.AsyncConnection.connect(conninfo, autocommit=True)
    try:
        cursor = await lock_conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (_SWEEP_LOCK_KEY,)
        )
        row = await cursor.fetchone()
        assert row is not None
        if not row[0]:
            raise SweepAlreadyRunning
        return await _sweep_locked(
            pool,
            source=source,
            lister=lister,
            sync=sync,
            audit=audit,
            main_group_email=main_group_email,
            allow_mass_removal=allow_mass_removal,
        )
    finally:
        await lock_conn.close()


async def _sweep_locked(
    pool: AsyncConnectionPool,
    *,
    source: MembershipSource | None,
    lister: GroupLister | None,
    sync: GroupSync,
    audit: AuditSink,
    main_group_email: Email,
    allow_mass_removal: bool,
) -> SweepReport:
    pull: PullReport | None = None
    if source is not None:
        members = await source.list_members()
        pull = await pull_roster(pool, members)
        if pull.lapse_refused > 0:
            raise RosterPullUnsafe(pull.lapse_refused)
    audiences = await desired_audiences(pool, main_group_email)
    if lister is None:
        logger.warning(
            "google reads are disabled: reporting desired state only, applying nothing"
        )
        for group_email, desired in sorted(audiences.items()):
            logger.info("desired state for %s: %d members", group_email, len(desired))
        return SweepReport(pull=pull, groups=(), lister_available=False)
    outcomes: list[GroupOutcome] = []
    for group_email, desired in sorted(audiences.items()):
        try:
            actual = await lister.list(group_email)
        except HttpError as error:
            logger.error(
                "sweep: listing %s failed (status %s); skipping this group",
                group_email,
                error.status_code,
            )
            outcomes.append(
                GroupOutcome(
                    group_email=group_email,
                    planned_adds=0,
                    planned_removes=0,
                    applied=0,
                    already_converged=0,
                    skipped=0,
                    refused=0,
                    failed=1,
                )
            )
            continue
        plan = plan_group(
            group_email, desired, actual, allow_mass_removal=allow_mass_removal
        )
        if plan.fuse_tripped:
            logger.error(
                "mass-removal fuse tripped on %s: refusing %d removals "
                "(%d actual members); adds still apply",
                group_email,
                plan.refused_removes,
                len(actual),
            )
        if len(plan.adds) > 0 or len(plan.removes) > 0:
            fresh = await desired_for_group(pool, group_email, main_group_email)
            plan = _recheck(plan, fresh)
        outcomes.append(await _apply(plan, sync=sync, audit=audit))
    return SweepReport(pull=pull, groups=tuple(outcomes), lister_available=True)


def _recheck(plan: GroupPlan, fresh: dict[str, UUID]) -> GroupPlan:
    """Drop plan entries a concurrent panel write has outdated.

    An add whose member is no longer desired, or a remove whose address now
    is, would undo a write that happened after the snapshot. The residue a
    recheck cannot catch self-heals on the next sweep.
    """
    return GroupPlan(
        group_email=plan.group_email,
        adds=tuple(a for a in plan.adds if a.address.casefold() in fresh),
        removes=tuple(r for r in plan.removes if r.casefold() not in fresh),
        refused_removes=plan.refused_removes,
    )


#: The audit actor for every sweep-applied change: a fixed system principal,
#: pseudonymized like any other actor.
SWEEP_ACTOR = "sweep"


async def _apply(plan: GroupPlan, *, sync: GroupSync, audit: AuditSink) -> GroupOutcome:
    """Adds first, then removes - a transient inconsistency errs toward access."""
    applied = converged = skipped = failed = 0
    for add in plan.adds:
        outcome = await sync.add(plan.group_email, add.address)
        applied, converged, skipped, failed = _tally(
            outcome, applied, converged, skipped, failed
        )
        if outcome is SyncOutcome.Applied:
            await record_best_effort(
                audit,
                "sweep_member_added",
                actor=SWEEP_ACTOR,
                subject=str(add.member_id),
                detail={"group": plan.group_email},
            )
    for address in plan.removes:
        outcome = await sync.remove(plan.group_email, address)
        applied, converged, skipped, failed = _tally(
            outcome, applied, converged, skipped, failed
        )
        if outcome is SyncOutcome.Applied:
            await record_best_effort(
                audit,
                "sweep_member_removed",
                actor=SWEEP_ACTOR,
                subject=None,
                detail={"group": plan.group_email},
            )
    return GroupOutcome(
        group_email=plan.group_email,
        planned_adds=len(plan.adds),
        planned_removes=len(plan.removes),
        applied=applied,
        already_converged=converged,
        skipped=skipped,
        refused=plan.refused_removes,
        failed=failed,
    )


def _tally(
    outcome: SyncOutcome, applied: int, converged: int, skipped: int, failed: int
) -> tuple[int, int, int, int]:
    if outcome is SyncOutcome.Applied:
        return applied + 1, converged, skipped, failed
    if outcome is SyncOutcome.AlreadyConverged:
        return applied, converged + 1, skipped, failed
    if outcome is SyncOutcome.Failed:
        return applied, converged, skipped, failed + 1
    return applied, converged, skipped + 1, failed
