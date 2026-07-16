"""The reconcile sweep's pure core: desired versus actual, per group.

`plan_group` is a pure function from one group's desired set and its actual
remote member list to the adds and removes that converge them. Three safety
rules live here, in testable logic rather than in the apply loop: only plain
USER members are ever removable (owners, managers, and nested groups survive
every sweep), a mass removal trips a fuse instead of executing, and an address
Google has already refused to hold is not offered again - while still counting
as desired, so it is never swept out either.

That last set - the addresses of `rosadmin.db.unmirrorable` - is read once per
run and grows as the run learns, which is why the sweep, not the Google
boundary, is what writes a refusal down.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from googleapiclient.errors import HttpError

from rosadmin.db.audit import AuditSink, record_best_effort
from rosadmin.db.directory import (
    BodyLinkRow,
    LinkTaken,
    all_bodies,
    is_group_provisioning_bootstrapped,
    mark_group_provisioning_bootstrapped,
    set_body_link,
)
from rosadmin.db.prune import prune_expired
from rosadmin.db.reconcile import desired_audiences, desired_for_group
from rosadmin.db.roster import PullReport, pull_roster
from rosadmin.db.unmirrorable import (
    is_refusal_learning_bootstrapped,
    mark_refusal_learning_bootstrapped,
    record_unmirrorable,
    unmirrorable_addresses,
)
from rosadmin.google_group import (
    SECURE_SETTINGS,
    SECURITY_LABEL,
    GroupMemberEntry,
    GroupsPermissionLevel,
    PropagationTimeout,
)
from rosadmin.group_naming import (
    GoogleGroupEmail,
    GoogleGroupName,
    GroupKind,
    GroupNameTooLong,
)
from rosadmin.group_sync import (
    SKIPPED,
    UNMIRRORABLE,
    GroupLister,
    GroupProvisioner,
    GroupSync,
    ProvisionedGroup,
    SyncOutcome,
)
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
    #: Desired members Google has already refused, so the sweep does not offer
    #: them. They carry their member id, which is what the log line names - the
    #: address never reaches the journal.
    excluded: tuple[PlannedAdd, ...]
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
    unmirrorable: set[str],
    allow_mass_removal: bool,
) -> GroupPlan:
    """Diff one group. `desired` keys and `unmirrorable` are casefolded addresses.

    Adds compare against every actual entry regardless of role or type - a
    member already present as a MANAGER must not be re-added as a MEMBER.
    Removes draw only from plain USER MEMBER entries, so owners, managers,
    and nested groups are structurally untouchable. Addresses compare
    casefolded because Google reports case it does not enforce.

    An address Google has refused is excluded from the adds - it lands in
    `excluded` instead - but it stays desired, and so is still counted against
    the removes. A member Google will not admit is not a member Google should
    evict: some are already in their group from before their account was
    deleted, and an outage answering 412 to every insert must not be able to
    empty a group. Dropping the address from `desired` outright - the shape
    `db.reconcile._admit` uses for `@example.com`, where the address can never
    be present in the first place - would instead make every refused member a
    stranger for the next sweep to remove.
    """
    present = {entry.email.casefold() for entry in actual}
    adds: list[PlannedAdd] = []
    excluded: list[PlannedAdd] = []
    for address, member_id in sorted(desired.items()):
        if address in present:
            continue
        planned = PlannedAdd(address=Email(address), member_id=member_id)
        if address in unmirrorable:
            excluded.append(planned)
        else:
            adds.append(planned)
    removable = [
        entry
        for entry in actual
        if entry.permission_level is GroupsPermissionLevel.Member
        and entry.type == "USER"
        and entry.email.casefold() not in desired
    ]
    removes = tuple(Email(entry.email) for entry in removable)
    over_budget = len(removes) > _removal_budget(len(actual))
    fuse_trips = over_budget and not allow_mass_removal
    return GroupPlan(
        group_email=group_email,
        adds=tuple(adds),
        removes=() if fuse_trips else removes,
        excluded=tuple(excluded),
        refused_removes=len(removes) if fuse_trips else 0,
    )


@dataclass(frozen=True)
class ProvisionConfig:
    """What provisioning needs beyond the pool: the email domain, the main
    group's display name (its address is `main_group_email`), and the armed-run
    creation cap."""

    domain: str
    main_group_name: str
    mass_creation_tripwire: int


@dataclass(frozen=True)
class ProvisionReport:
    """One run's provisioning slice - counts only, never addresses."""

    created: int
    adopted: int
    already_linked: int
    diverged: int
    refused_over_cap: int
    failed: int

    @property
    def has_failures(self) -> bool:
        return self.failed > 0 or self.refused_over_cap > 0


@dataclass(frozen=True)
class _PlannedGroup:
    """One group the plan wants to exist: its address, display name, and - for a
    body group - the body and column to link once it resolves."""

    email: Email
    name: str
    body_id: UUID | None  # None for the main group, which links nothing
    kind: GroupKind | None


def _plan_body(body: BodyLinkRow, config: ProvisionConfig) -> list[_PlannedGroup]:
    """A body's leaders and editors group, named by the pure rules. Raises
    `GroupNameTooLong` when the body cannot be named within Google's caps - the
    caller catches it so one un-nameable body fails alone rather than aborting
    the run."""
    planned: list[_PlannedGroup] = []
    for kind in (GroupKind.Leaders, GroupKind.Editors):
        email = Email(
            str(GoogleGroupEmail(body.name, body.body_type, kind, config.domain))
        )
        name = str(GoogleGroupName(body.name, body.body_type, kind))
        planned.append(
            _PlannedGroup(email=email, name=name, body_id=body.id, kind=kind)
        )
    return planned


def _is_linked(body: BodyLinkRow) -> bool:
    """A body is already linked once both address columns are set. Keying the
    skip on the stored columns (not on whether today's naming reproduces them)
    means a renamed or hand-linked body keeps its existing groups instead of
    being re-minted and silently repointed."""
    return (
        body.leader_google_group_email is not None
        and body.member_google_group_email is not None
    )


def _plan_pending(
    bodies: list[BodyLinkRow], config: ProvisionConfig, main_email: Email
) -> tuple[list[_PlannedGroup], int]:
    """The groups a run must ensure, and how many bodies it could not name.

    The main group is always ensured. A body linked on both columns is already
    done and contributes nothing. A body that overflows a Google cap is counted
    in the second return value and skipped, so one un-nameable body never aborts
    the whole run.
    """
    pending = [
        _PlannedGroup(
            email=main_email, name=config.main_group_name, body_id=None, kind=None
        )
    ]
    name_failures = 0
    for body in bodies:
        if _is_linked(body):
            continue
        try:
            pending.extend(_plan_body(body, config))
        except GroupNameTooLong as error:
            logger.error("cannot name groups for body %s: %s", body.id, error)
            name_failures += 1
    return pending, name_failures


def _diverged(seen: ProvisionedGroup) -> bool:
    """True when an adopted group's settings or labels differ from the secure
    defaults - the sweep warns, never rewrites."""
    settings_ok = all(seen.settings.get(k) == v for k, v in SECURE_SETTINGS.items())
    labels_ok = seen.labels == SECURITY_LABEL.get("labels", {})
    return not (settings_ok and labels_ok)


def _tripwire_refuses(bootstrapped: bool, creations: int, cap: int) -> bool:
    """Whether an armed run must refuse this batch of creations.

    The first (not-yet-bootstrapped) run mints freely however large - it is the
    seeding run the cap is armed after. Once bootstrapped, a batch larger than
    the cap is a poisoned plan (a truncated `all_bodies`, a mock wired live) and
    is refused wholesale rather than obediently created.
    """
    return bootstrapped and creations > cap


async def _provision(
    pool: AsyncConnectionPool,
    provisioner: GroupProvisioner,
    config: ProvisionConfig,
    main_email: Email,
    *,
    dry_run: bool,
) -> ProvisionReport:
    """Ensure every body's groups and the main group exist and are linked.

    The bootstrap marker waives the cap for the first run and arms it after; an
    armed run that would create more than the cap creates nothing and fails. A
    dry run does the real existence reads and the recorded `ensure` calls but
    writes nothing to the database - no link, no bootstrap marker - so a
    rehearsal cannot arm state a later real run would then skip.
    """
    bodies = await all_bodies(pool)
    already_linked = sum(1 for body in bodies if _is_linked(body))
    pending, failed = _plan_pending(bodies, config, main_email)

    # Size the tripwire: how many pending groups do not yet exist remotely.
    to_create = [g for g in pending if not await provisioner.exists(g.email)]
    bootstrapped = await is_group_provisioning_bootstrapped(pool)
    if _tripwire_refuses(bootstrapped, len(to_create), config.mass_creation_tripwire):
        logger.error(
            "mass-creation tripwire: %d new groups exceed the cap of %d; creating "
            "none this run",
            len(to_create),
            config.mass_creation_tripwire,
        )
        return ProvisionReport(
            created=0,
            adopted=0,
            already_linked=already_linked,
            diverged=0,
            refused_over_cap=len(to_create),
            failed=failed,
        )

    creating = {g.email for g in to_create}
    created = adopted = diverged = 0
    body_links: dict[UUID, dict[GroupKind, Email]] = {}
    for group in pending:
        try:
            seen = await provisioner.ensure(group.email, group.name)
        except (HttpError, PropagationTimeout) as error:
            logger.error("provisioning %s failed: %s", group.email, error)
            failed += 1
            continue
        if group.email in creating:
            created += 1
        else:
            adopted += 1
            if _diverged(seen):
                diverged += 1
                logger.warning(
                    "adopted %s has settings that diverge from the secure defaults",
                    group.email,
                )
        if group.body_id is not None and group.kind is not None:
            body_links.setdefault(group.body_id, {})[group.kind] = group.email

    failed = await _link_bodies(pool, body_links, failed, dry_run=dry_run)

    if not bootstrapped and created > 0:
        if dry_run:
            logger.info("would arm the group-provisioning bootstrap marker")
        else:
            await mark_group_provisioning_bootstrapped(pool)
    return ProvisionReport(
        created=created,
        adopted=adopted,
        already_linked=already_linked,
        diverged=diverged,
        refused_over_cap=0,
        failed=failed,
    )


async def _link_bodies(
    pool: AsyncConnectionPool,
    body_links: dict[UUID, dict[GroupKind, Email]],
    failed: int,
    *,
    dry_run: bool,
) -> int:
    """Write each newly provisioned body's two resolved addresses. A `LinkTaken`
    clash is a per-body failure, never a silent repoint; a body missing one
    provisioned side stays unlinked and counts as failed. A dry run logs the link
    it would write and touches nothing."""
    for body_id, links in body_links.items():
        if GroupKind.Leaders not in links or GroupKind.Editors not in links:
            failed += 1  # one side failed to provision; leave the body unlinked
            continue
        if dry_run:
            logger.info("would link body %s", body_id)
            continue
        try:
            await set_body_link(
                pool, body_id, links[GroupKind.Leaders], links[GroupKind.Editors]
            )
        except LinkTaken as taken:
            logger.error("refusing to link body %s: %s", body_id, taken)
            failed += 1
    return failed


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
    #: Desired members not offered because Google has already refused their
    #: address. Steady-state - the size of the sediment - and not a failure.
    excluded: int
    #: Refusals Google issued this run that it had not issued before - counted as
    #: they are received, not as they are stored, so that no failure of the store
    #: can quiet the alarm that reads them. News, not sediment.
    unmirrorable: int
    refused: int
    failed: int


#: How many refusals one armed run may write down. A steady state meets a
#: handful - a member joins carrying an address with no Google account behind it -
#: so a run meeting dozens is not learning about the roster, it is learning that
#: something outside it has changed: a scope withdrawn, the security label
#: misapplied, Google answering 412 to everything. A batch past this ceiling is
#: refused wholesale rather than recorded, on the same principle as the removal
#: fuse and the creation tripwire: the point of a fuse is that it will not do the
#: thing, and a fuse that merely reported afterwards would leave the roster
#: suppressed for a season and go quiet on the very next run, having nothing left
#: to learn.
REFUSAL_FUSE_CEILING = 25


@dataclass(frozen=True)
class RefusalReport:
    """What one run did with the refusals Google issued it.

    `received` counts them as Google gave them, so no failure of the store can
    quiet what reads it. `recorded` is what actually landed. `refused` is the fuse
    saying no - the batch was too large to believe, and none of it was written.
    """

    received: int
    recorded: int
    refused: int

    @property
    def has_failures(self) -> bool:
        return self.refused > 0 or self.recorded < self.received - self.refused


@dataclass(frozen=True)
class SweepReport:
    """One run's outcome: the pull it began with and every group it touched."""

    pull: PullReport | None
    groups: tuple[GroupOutcome, ...]
    #: False when the environment forbade Google reads entirely - the run
    #: reported desired state and applied nothing.
    lister_available: bool
    provision: ProvisionReport | None = None
    refusals: RefusalReport | None = None

    @property
    def has_failures(self) -> bool:
        provision_failed = self.provision is not None and self.provision.has_failures
        refusals_failed = self.refusals is not None and self.refusals.has_failures
        return (
            provision_failed
            or refusals_failed
            or any(g.failed > 0 or g.refused > 0 for g in self.groups)
        )


async def run_sweep(
    pool: AsyncConnectionPool,
    *,
    source: MembershipSource | None,
    lister: GroupLister | None,
    sync: GroupSync,
    audit: AuditSink,
    main_group_email: Email,
    provisioner: GroupProvisioner | None = None,
    provision: ProvisionConfig | None = None,
    allow_mass_removal: bool = False,
    dry_run: bool = False,
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
            provisioner=provisioner,
            provision=provision,
            allow_mass_removal=allow_mass_removal,
            dry_run=dry_run,
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
    provisioner: GroupProvisioner | None,
    provision: ProvisionConfig | None,
    allow_mass_removal: bool,
    dry_run: bool,
) -> SweepReport:
    # Housekeeping first, under the sweep lock: clear the expired auth rows that only
    # ever grow. Runs regardless of pull or lister, independent of the reconcile
    # itself, so even a quiet sweep keeps jti_replay and rate_limit_counters bounded.
    pruned = await prune_expired(pool)
    if pruned.jti or pruned.rate_limit:
        logger.info(
            "housekeeping: pruned %d expired jti and %d closed rate-limit windows",
            pruned.jti,
            pruned.rate_limit,
        )
    pull: PullReport | None = None
    if source is not None:
        members = await source.list_members()
        pull = await pull_roster(pool, members)
        if pull.lapse_refused > 0:
            raise RosterPullUnsafe(pull.lapse_refused)
    provision_report: ProvisionReport | None = None
    if provisioner is not None and provision is not None:
        provision_report = await _provision(
            pool, provisioner, provision, main_group_email, dry_run=dry_run
        )
    elif provision is not None:
        # Google reads/writes are off (the env dry-run toggle): name what we
        # would mint, touch nothing.
        pending, _ = _plan_pending(await all_bodies(pool), provision, main_group_email)
        for group in pending:
            logger.info("would provision %s (%s)", group.email, group.name)
    audiences = await desired_audiences(pool, main_group_email)
    if lister is None:
        logger.warning(
            "google reads are disabled: reporting desired state only, applying nothing"
        )
        for group_email, desired in sorted(audiences.items()):
            logger.info("desired state for %s: %d members", group_email, len(desired))
        return SweepReport(
            pull=pull,
            groups=(),
            lister_available=False,
            provision=provision_report,
        )
    # Read once for the whole run, then deliberately mutated by `_apply` as each
    # refusal is met: a group swept later never re-offers an address an earlier
    # group has just proved bad, so a run meets each address once rather than once
    # per group it appears in.
    unmirrorable = await unmirrorable_addresses(pool)
    # Held, not written, until the run is over: only the size of the whole batch
    # can tell a trickle of new refusals from an event, and `_commit_refusals` is
    # where that judgement is made.
    refusals: list[Refusal] = []
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
                    excluded=0,
                    unmirrorable=0,
                    refused=0,
                    failed=1,
                )
            )
            continue
        plan = plan_group(
            group_email,
            desired,
            actual,
            unmirrorable=unmirrorable,
            allow_mass_removal=allow_mass_removal,
        )
        if plan.fuse_tripped:
            logger.error(
                "mass-removal fuse tripped on %s: refusing %d removals "
                "(%d actual members); adds still apply",
                group_email,
                plan.refused_removes,
                len(actual),
            )
        if len(plan.adds) > 0 or len(plan.removes) > 0 or len(plan.excluded) > 0:
            fresh = await desired_for_group(pool, group_email, main_group_email)
            plan = _recheck(plan, fresh)
        outcomes.append(
            await _apply(
                plan,
                sync=sync,
                audit=audit,
                lister=lister,
                unmirrorable=unmirrorable,
                refusals=refusals,
            )
        )
    return SweepReport(
        pull=pull,
        groups=tuple(outcomes),
        lister_available=True,
        provision=provision_report,
        refusals=await _commit_refusals(pool, refusals, dry_run=dry_run),
    )


async def _commit_refusals(
    pool: AsyncConnectionPool, refusals: list[Refusal], *, dry_run: bool
) -> RefusalReport:
    """Write down what Google refused this run - or, past the fuse, refuse to.

    Nothing is written until the whole run has been seen, because the size of the
    batch is the only thing that tells sediment from an event. A first run meets
    the entire standing cohort at once and writes it freely, arming the fuse
    behind itself; an armed run that suddenly meets dozens is being told something
    about Google, not about the roster, and recording that would suppress those
    members for a season and then - having nothing left to learn - report itself
    green forever after. So it records none of them, says so, and fails. The
    addresses are simply offered again next run, which is where they started.
    """
    received = len(refusals)
    if dry_run:
        if received > 0:
            logger.info("dry-run: would record %d refused addresses", received)
        return RefusalReport(received=received, recorded=0, refused=0)
    bootstrapped = await is_refusal_learning_bootstrapped(pool)
    if bootstrapped and received > REFUSAL_FUSE_CEILING:
        logger.error(
            "refusal fuse: google refused %d addresses it had not refused before, "
            "past the ceiling of %d; recording none of them. nobody was removed and "
            "nobody is newly withheld - the sweep will offer them all again next run "
            "- but a refusal on this scale is a change outside the roster, and the "
            "runbook says what to look at",
            received,
            REFUSAL_FUSE_CEILING,
        )
        return RefusalReport(received=received, recorded=0, refused=received)
    recorded = 0
    for refusal in refusals:
        if await _remember_refusal(pool, refusal):
            recorded += 1
    if not bootstrapped and recorded > 0:
        await mark_refusal_learning_bootstrapped(pool)
    return RefusalReport(received=received, recorded=recorded, refused=0)


def _recheck(plan: GroupPlan, fresh: dict[str, UUID]) -> GroupPlan:
    """Drop plan entries a concurrent panel write has outdated.

    An add whose member is no longer desired, or a remove whose address now
    is, would undo a write that happened after the snapshot. An exclusion is
    filtered on the same rule as an add, since a member dropped from desired
    since the snapshot is no longer being withheld from anything. The residue a
    recheck cannot catch self-heals on the next sweep.
    """
    return GroupPlan(
        group_email=plan.group_email,
        adds=tuple(a for a in plan.adds if a.address.casefold() in fresh),
        removes=tuple(r for r in plan.removes if r.casefold() not in fresh),
        excluded=tuple(e for e in plan.excluded if e.address.casefold() in fresh),
        refused_removes=plan.refused_removes,
    )


#: The audit actor for every sweep-applied change: a fixed system principal,
#: pseudonymized like any other actor.
SWEEP_ACTOR = "sweep"


class Presence(Enum):
    """What Google says about a group when an insert's 404 makes it matter.

    Three states, not a bool: "the group is gone" and "Google would not tell me"
    lead to the same caution but are not the same fact, and an operator reading
    `google no longer has everyone@...` when the truth was an expired token has
    been told something false about their tenant.
    """

    Present = "present"
    Gone = "gone"
    Unknown = "unknown"


@dataclass(frozen=True)
class Refusal:
    """One address Google refused, and the member and group it was refused for.

    Held rather than written the moment it is met: a refusal is only believable
    once the group it was refused on is known to still exist, and only recordable
    once the size of the run's whole batch is known.
    """

    address: Email
    member_id: UUID
    group_email: Email
    outcome: SyncOutcome


async def _presence(lister: GroupLister, group_email: Email) -> Presence:
    """Ask Google whether the group is still there - only ever to read a 404.

    An insert answers `404 notFound` both for an address Google does not have
    and for a group it does not have, and the sweep's opening listing only
    vouches for the group as of that moment: a bulk run paces its writes, so its
    last insert can land half an hour after the listing that spoke for it. This
    settles the ambiguity before a member's address is written off for a season
    on the strength of it.
    """
    try:
        found = await lister.exists(group_email)
    except HttpError as error:
        logger.error(
            "sweep: google would not say whether %s still exists (status %s); "
            "treating its refusals as unreadable rather than as verdicts",
            group_email,
            error.status_code,
        )
        return Presence.Unknown
    return Presence.Present if found else Presence.Gone


async def _remember_refusal(pool: AsyncConnectionPool, refusal: Refusal) -> bool:
    """Write one refusal down, and never let that write end the sweep.

    Like the audit row beside it, this records something that has already
    happened out at Google. A database that cannot take it down is worth an
    operator's attention, but it is not worth abandoning what the run has not
    reached yet: the unrecorded address is simply offered - and refused - again
    next run, which is exactly where it started. Answers whether the row landed,
    because a refusal that was not written down is one the next run must go and
    ask about again, and the report says so.

    The failure is named by its class and SQLSTATE, never by the server's own
    message: Postgres puts the whole offending row in the detail of a constraint
    violation, and that row carries the member's address.
    """
    try:
        await record_unmirrorable(pool, refusal.address, refusal.outcome)
    except psycopg.Error as error:
        logger.error(
            "sweep: could not record google's refusal of member %s on %s (%s): "
            "%s, sqlstate %s",
            refusal.member_id,
            refusal.group_email,
            refusal.outcome.value,
            type(error).__name__,
            error.sqlstate,
        )
        return False
    logger.info(
        "sweep: google refuses member %s on %s (%s); "
        "the address will not be offered again this window",
        refusal.member_id,
        refusal.group_email,
        refusal.outcome.value,
    )
    return True


async def _apply(
    plan: GroupPlan,
    *,
    sync: GroupSync,
    audit: AuditSink,
    lister: GroupLister,
    unmirrorable: set[str],
    refusals: list[Refusal],
) -> GroupOutcome:
    """Adds first, then removes - a transient inconsistency errs toward access."""
    tally: Counter[SyncOutcome] = Counter()
    presence: Presence | None = None
    found: list[Refusal] = []
    for add in plan.adds:
        outcome = await sync.add(plan.group_email, add.address)
        if outcome is SyncOutcome.AddressNotFound:
            if presence is None:
                presence = await _presence(lister, plan.group_email)
            if presence is Presence.Gone:
                return _abandoned(plan, tally, presence)
            if presence is Presence.Unknown:
                # The 404 may have been about the group. Unreadable, so unrecorded.
                tally[SyncOutcome.Failed] += 1
                continue
        tally[outcome] += 1
        if outcome is SyncOutcome.Applied:
            await record_best_effort(
                audit,
                "sweep_member_added",
                actor=SWEEP_ACTOR,
                subject=str(add.member_id),
                detail={"group": plan.group_email},
            )
        elif outcome in UNMIRRORABLE:
            found.append(
                Refusal(
                    address=add.address,
                    member_id=add.member_id,
                    group_email=plan.group_email,
                    outcome=outcome,
                )
            )
            unmirrorable.add(add.address.casefold())
    # The adds took as long as they took. A 404 read at the start of them is only
    # as good as the group still being there at the end, so ask once more before
    # any of it is believed.
    if any(r.outcome is SyncOutcome.AddressNotFound for r in found):
        presence = await _presence(lister, plan.group_email)
        if presence is not Presence.Present:
            return _abandoned(plan, tally, presence)
    refusals.extend(found)

    removes_converged = 0
    for address in plan.removes:
        outcome = await sync.remove(plan.group_email, address)
        tally[outcome] += 1
        if outcome is SyncOutcome.Applied:
            await record_best_effort(
                audit,
                "sweep_member_removed",
                actor=SWEEP_ACTOR,
                subject=None,
                detail={"group": plan.group_email},
            )
        elif outcome is SyncOutcome.AlreadyConverged:
            removes_converged += 1
    # Every removal answering "already gone", with nothing this run having proved
    # the group alive, is what a deleted group looks like from the outside - the
    # one shape of it the adds cannot catch, since a converged group plans none.
    if (
        len(plan.removes) > 0
        and removes_converged == len(plan.removes)
        and tally[SyncOutcome.Applied] == 0
        and await _presence(lister, plan.group_email) is Presence.Gone
    ):
        logger.error(
            "sweep: every removal on %s reported the member already gone, and google "
            "no longer has the group; it converged with nothing, not with the records",
            plan.group_email,
        )
        return GroupOutcome(
            group_email=plan.group_email,
            planned_adds=len(plan.adds),
            planned_removes=len(plan.removes),
            applied=0,
            already_converged=0,
            skipped=sum(tally[outcome] for outcome in SKIPPED),
            excluded=len(plan.excluded),
            unmirrorable=0,
            refused=plan.refused_removes,
            failed=len(plan.removes),
        )

    _log_excluded(plan)
    return GroupOutcome(
        group_email=plan.group_email,
        planned_adds=len(plan.adds),
        planned_removes=len(plan.removes),
        applied=tally[SyncOutcome.Applied],
        already_converged=tally[SyncOutcome.AlreadyConverged],
        skipped=sum(tally[outcome] for outcome in SKIPPED),
        excluded=len(plan.excluded),
        unmirrorable=sum(tally[outcome] for outcome in UNMIRRORABLE),
        refused=plan.refused_removes,
        failed=tally[SyncOutcome.Failed],
    )


def _abandoned(
    plan: GroupPlan, tally: Counter[SyncOutcome], presence: Presence
) -> GroupOutcome:
    """The outcome for a group Google could not vouch for: everything left undone.

    Whatever the plan still wanted is counted as failed - the adds never
    attempted along with the one whose 404 raised the question, and the removes
    the sweep declined to make against a group it cannot see. That is what makes
    the run red, and a red run is the point: a linked body whose remote group has
    gone missing is a hole in the records, not a quiet no-op.

    Nothing this group refused is carried out of here, so the refusals it met are
    dropped rather than written - which is what lets the line below say so
    truthfully.
    """
    if presence is Presence.Gone:
        logger.error(
            "sweep: google no longer has %s; leaving it alone and recording nothing "
            "against the members it refused",
            plan.group_email,
        )
    else:
        logger.error(
            "sweep: cannot confirm %s still exists; leaving it alone this run",
            plan.group_email,
        )
    settled = (
        tally[SyncOutcome.Applied]
        + tally[SyncOutcome.AlreadyConverged]
        + sum(tally[outcome] for outcome in SKIPPED)
        + sum(tally[outcome] for outcome in UNMIRRORABLE)
    )
    return GroupOutcome(
        group_email=plan.group_email,
        planned_adds=len(plan.adds),
        planned_removes=len(plan.removes),
        applied=tally[SyncOutcome.Applied],
        already_converged=tally[SyncOutcome.AlreadyConverged],
        skipped=sum(tally[outcome] for outcome in SKIPPED),
        excluded=len(plan.excluded),
        unmirrorable=sum(tally[outcome] for outcome in UNMIRRORABLE),
        refused=plan.refused_removes,
        failed=len(plan.adds) - settled + len(plan.removes),
    )


def _log_excluded(plan: GroupPlan) -> None:
    """The count at INFO, the member ids at DEBUG - never the addresses.

    The count is the standing size of the group's refused cohort and belongs in
    every run's journal. The ids are hundreds of lines on the main group, so
    they wait for someone who has turned DEBUG on to go looking for them.
    """
    if len(plan.excluded) == 0:
        return
    logger.info(
        "sweep: %s has %d desired members google has already refused; not offering them",
        plan.group_email,
        len(plan.excluded),
    )
    for withheld in plan.excluded:
        logger.debug(
            "sweep: %s withholds member %s", plan.group_email, withheld.member_id
        )
