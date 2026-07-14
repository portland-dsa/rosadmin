# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
"""Steps for the reconcile sweep feature: seeded rows against a fake Workspace."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import psycopg
from behave import given, then, when

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.google_group import (
    SECURE_SETTINGS,
    SECURITY_LABEL,
    GroupMemberEntry,
    GroupsPermissionLevel,
)
from rosadmin.group_sync import (
    ProvisionedGroup,
    RecordingGroupSync,
    RecordingProvisioner,
    SyncOutcome,
    _skip_gate,
)
from rosadmin.membership.source import Email
from rosadmin.reconcile import ProvisionConfig, run_sweep


class FakeWorkspace:
    """An in-memory Workspace: lister and sync port over one dict of groups.

    Runs the real `_skip_gate` so gate behavior cannot drift from the
    production sync, then mutates state and answers with the outcomes the
    real mirror would give (409-on-add and 404-on-remove map to
    AlreadyConverged; an address in `no_google_account` draws the refusal a
    security group gives an address with no Google identity behind it).
    """

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, GroupMemberEntry]] = {}
        self.settings: dict[str, dict[str, str]] = {}
        self.labels: dict[str, dict[str, str]] = {}
        #: Casefolded addresses Google has no account for, whatever group they
        #: are offered to - a fact about the address, as it is at Google.
        self.no_google_account: set[str] = set()
        #: Casefolded addresses Google is authoritative for and does not have -
        #: a deleted or mistyped Gmail, which draws a 404 rather than a 412.
        self.unknown_address: set[str] = set()
        #: Groups deleted at Google after the sweep listed them - the race the
        #: presence check exists for. `list` still answers (the sweep read it
        #: before the deletion), while `exists` and `add` behave as Google does
        #: afterwards: the insert 404s exactly as an unknown address would, which
        #: is the ambiguity the sweep has to resolve before it blames a member for
        #: a hole in the group.
        self.deleted_after_listing: set[str] = set()
        #: Every (group, address) this run offered to the sync port. Cleared at
        #: the start of each sweep, so a Then step reads the latest run alone
        #: and a scenario can assert what a *second* sweep no longer offers.
        self.offered: list[tuple[str, str]] = []

    async def exists(self, group_email: Email) -> bool:
        return (
            group_email not in self.deleted_after_listing and group_email in self.groups
        )

    async def ensure(self, group_email: Email, name: str) -> ProvisionedGroup:
        if group_email not in self.groups:
            self.groups[group_email] = {}
            self.settings[group_email] = {k: str(v) for k, v in SECURE_SETTINGS.items()}
            self.labels[group_email] = dict(SECURITY_LABEL.get("labels", {}))
        return ProvisionedGroup(
            settings=self.settings[group_email], labels=self.labels[group_email]
        )

    def seed(
        self,
        group_email: str,
        address: str,
        *,
        permission: str = "MEMBER",
        type: str = "USER",
    ) -> None:
        self.groups.setdefault(group_email, {})[address.casefold()] = GroupMemberEntry(
            email=address,
            permission_level=GroupsPermissionLevel(permission),
            status="ACTIVE",
            type=type,
        )

    def holds(self, group_email: str, address: str) -> bool:
        return address.casefold() in self.groups.get(group_email, {})

    async def list(self, group_email: Email) -> list[GroupMemberEntry]:
        return list(self.groups.get(group_email, {}).values())

    async def add(self, group_email: Email | None, member_email: Email) -> SyncOutcome:
        gated = _skip_gate(group_email, member_email, expect_example_emails=True)
        if isinstance(gated, SyncOutcome):
            return gated
        self.offered.append((gated, member_email.casefold()))
        if gated in self.deleted_after_listing:
            # Google answers an insert into a group it no longer has exactly as it
            # answers an insert of an address it does not have.
            return SyncOutcome.AddressNotFound
        entries = self.groups.setdefault(gated, {})
        if member_email.casefold() in self.no_google_account:
            return SyncOutcome.NoGoogleAccount
        if member_email.casefold() in self.unknown_address:
            return SyncOutcome.AddressNotFound
        if member_email.casefold() in entries:
            return SyncOutcome.AlreadyConverged
        entries[member_email.casefold()] = GroupMemberEntry(
            email=member_email,
            permission_level=GroupsPermissionLevel.Member,
            status="ACTIVE",
            type="USER",
        )
        return SyncOutcome.Applied

    async def remove(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        gated = _skip_gate(group_email, member_email, expect_example_emails=True)
        if isinstance(gated, SyncOutcome):
            return gated
        entries = self.groups.get(gated, {})
        if member_email.casefold() not in entries:
            return SyncOutcome.AlreadyConverged
        del entries[member_email.casefold()]
        return SyncOutcome.Applied


def _st_id() -> int:
    """A fresh, distinct `st_id` for a seeded member - the value itself is arbitrary."""
    return uuid4().int & 0x7FFF_FFFF_FFFF_FFFF


def _ensure_context(context) -> None:
    if hasattr(context, "workspace"):
        return
    context.workspace = FakeWorkspace()
    context.member_ids = {}
    context.body_ids = {}


@given('the main group is "{email}"')
def step_main_group(context, email):
    context.main_group = email


@given('the body "{name}" is linked to "{leader}" and "{member}"')
def step_linked_body(context, name, leader, member):
    _ensure_context(context)
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO leadership_bodies "
            "(name, body_type, leader_google_group_email, member_google_group_email) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (name, "Committee", leader, member),
        )
        row = cursor.fetchone()
    assert row is not None
    context.body_ids[name] = row[0]


@given('a member "{email}" in standing "{standing}"')
def step_member(context, email, standing):
    _ensure_context(context)
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO members (st_id, email, standing) VALUES (%s, %s, %s) "
            "RETURNING id",
            (_st_id(), email, standing),
        )
        row = cursor.fetchone()
    assert row is not None
    context.member_ids[email] = row[0]


@given('"{email}" holds a "{role}" row on "{body}"')
def step_holds_role(context, email, role, body):
    member_id = context.member_ids[email]
    body_id = context.body_ids[body]
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO body_memberships (member_id, body_id, role) VALUES (%s, %s, %s)",
            (member_id, body_id, role),
        )


@when('Ralsei sets "{email}" to standing "{standing}"')
@when('Susie sets "{email}" to standing "{standing}"')
def step_set_standing(context, email, standing):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE members SET standing = %s WHERE email = %s", (standing, email)
        )


@given('the group "{group:S}" already holds "{address:S}"')
def step_seed_member(context, group, address):
    _ensure_context(context)
    context.workspace.seed(group, address)


@given('the group "{group:S}" already holds "{address:S}" as a "{permission}"')
def step_seed_with_permission(context, group, address, permission):
    _ensure_context(context)
    context.workspace.seed(group, address, permission=permission)


@given('the group "{group:S}" already holds "{address:S}" as a nested group')
def step_seed_nested_group(context, group, address):
    _ensure_context(context)
    context.workspace.seed(group, address, type="GROUP")


@given('the group "{group:S}" already holds {count:d} seeded members')
def step_seed_many(context, group, count):
    _ensure_context(context)
    for i in range(count):
        context.workspace.seed(group, f"seed{i}@example.net")


@given('Google has no account for "{address:S}"')
def step_no_google_account(context, address):
    _ensure_context(context)
    context.workspace.no_google_account.add(address.casefold())


@given('Google does not have the address "{address:S}"')
def step_unknown_address(context, address):
    _ensure_context(context)
    context.workspace.unknown_address.add(address.casefold())


@given('Susie has deleted the group "{group:S}" at Google')
def step_lost_group(context, group):
    _ensure_context(context)
    context.workspace.deleted_after_listing.add(group)


@given("{count:d} members in good standing Google has no account for")
def step_many_refused_members(context, count):
    _ensure_context(context)
    for i in range(count):
        address = f"refused{i}@example.net"
        step_member(context, address, "good_standing")
        context.workspace.no_google_account.add(address)


@given("refusal learning has already bootstrapped")
def step_refusals_bootstrapped(context):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute("UPDATE bootstrap_state SET bootstrapped_refusal_learning = true")


@when("Ralsei fixes Google and the sweep runs again")
def step_google_fixed_and_sweep(context):
    """The outage is over: Google takes the addresses it was refusing.

    The point of the fuse is that the run before this one wrote nothing down, so
    there is nothing to clear and nobody stayed withheld - the members simply land
    on the next run.
    """
    context.workspace.no_google_account.clear()
    _run_sweep(context, dry_run=False)


@then("{count:d} addresses are recorded unmirrorable")
def step_count_recorded(context, count):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute("SELECT count(*) FROM unmirrorable_addresses")
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == count


@given('the group "{group:S}" already exists')
def step_group_exists_empty(context, group):
    """An empty group that is really there - what a refusal has to be read against.

    Without it a scenario would lean on the fake's own bookkeeping to conjure the
    group, and the presence check would be answering a question nothing asked.
    """
    _ensure_context(context)
    context.workspace.groups.setdefault(group, {})


@given('"{address:S}" was refused {days:d} days ago')
def step_refused_days_ago(context, address, days):
    """Backdate a refusal, which is the only way a scenario reaches the retry
    window: `RETRY_AFTER` is measured against `observed_at`, and a row this run
    writes is always fresh."""
    _ensure_context(context)
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO unmirrorable_addresses (address, reason, observed_at) "
            "VALUES (%s, %s, now() - make_interval(days => %s::int))",
            (address.casefold(), SyncOutcome.NoGoogleAccount.value, days),
        )


def _run_sweep(context, *, dry_run: bool) -> None:
    workspace = context.workspace
    workspace.offered.clear()
    sync = RecordingGroupSync() if dry_run else workspace
    context.recording = sync if dry_run else None
    context.audit = RecordingAuditSink()

    async def _go() -> None:
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        try:
            context.report = await run_sweep(
                pool,
                source=None,
                lister=workspace,
                sync=sync,
                audit=context.audit,
                main_group_email=Email(context.main_group),
                dry_run=dry_run,
            )
        finally:
            await pool.close()

    asyncio.run(_go())


@when("the sweep runs")
def step_sweep_runs(context):
    _run_sweep(context, dry_run=False)


@when("the sweep runs in dry-run mode")
def step_sweep_runs_dry(context):
    _run_sweep(context, dry_run=True)


@then('the group "{group:S}" contains "{address:S}"')
def step_group_contains(context, group, address):
    assert context.workspace.holds(group, address)


@then('the group "{group:S}" does not contain "{address:S}"')
def step_group_not_contains(context, group, address):
    assert not context.workspace.holds(group, address)


@then('an audit row records action "{action}"')
def step_audit_action(context, action):
    assert any(r.action == action for r in context.audit.records)


@then('the dry-run recorded a planned add to "{group:S}"')
def step_dry_run_recorded_add(context, group):
    assert any(
        op == "add" and g == group
        for op, g, _member, _outcome in context.recording.recorded
    )


@then('the sweep report marks "{group:S}" as refused')
def step_marks_refused(context, group):
    outcome = next(g for g in context.report.groups if g.group_email == group)
    assert outcome.refused > 0


@then('the group "{group:S}" still holds {count:d} seeded members')
def step_still_holds_seeded(context, group, count):
    for i in range(count):
        assert context.workspace.holds(group, f"seed{i}@example.net")


@then("the sweep run reports failure")
def step_reports_failure(context):
    assert context.report.has_failures


@then("the sweep run reports success")
def step_reports_success(context):
    assert not context.report.has_failures


@then('the sweep did not offer "{address:S}" to "{group:S}"')
def step_did_not_offer(context, address, group):
    assert (group, address.casefold()) not in context.workspace.offered


@then('"{address:S}" is recorded unmirrorable for reason "{reason}"')
def step_recorded_unmirrorable(context, address, reason):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "SELECT reason FROM unmirrorable_addresses WHERE address = %s",
            (address.casefold(),),
        )
        row = cursor.fetchone()
    assert row is not None, "the sweep recorded no refusal for that address"
    assert row[0] == reason


@then("no address is recorded unmirrorable")
def step_nothing_recorded(context):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute("SELECT count(*) FROM unmirrorable_addresses")
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == 0, "a refusal was written off against a member"


@given('an unlinked body "{name}" of type "{body_type}"')
def step_unlinked_body(context, name, body_type):
    _ensure_context(context)
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "INSERT INTO leadership_bodies (name, body_type) VALUES (%s, %s) RETURNING id",
            (name, body_type),
        )
        row = cursor.fetchone()
    assert row is not None
    context.body_ids[name] = row[0]


@given('{count:d} unlinked bodies of type "{body_type}"')
def step_many_unlinked_bodies(context, count, body_type):
    _ensure_context(context)
    for i in range(count):
        with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO leadership_bodies (name, body_type) VALUES (%s, %s)",
                (f"Body {i}", body_type),
            )


@given("provisioning has already bootstrapped")
def step_already_bootstrapped(context):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE bootstrap_state SET bootstrapped_group_provisioning = true"
        )


@given("the mass-creation tripwire is {cap:d}")
def step_tripwire(context, cap):
    context.tripwire = cap


@given('the group "{email:S}" already exists with slack settings')
def step_preexisting_slack(context, email):
    _ensure_context(context)
    context.workspace.groups[email] = {}
    context.workspace.settings[email] = {"whoCanJoin": "ALL_IN_DOMAIN_CAN_JOIN"}
    context.workspace.labels[email] = {}


@when("the sweep runs with provisioning")
def step_sweep_with_provisioning(context):
    _ensure_context(context)
    config = ProvisionConfig(
        domain="example.net",
        main_group_name="Everyone",
        mass_creation_tripwire=getattr(context, "tripwire", 10),
    )
    context.audit = RecordingAuditSink()

    async def _go():
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        try:
            context.report = await run_sweep(
                pool,
                source=None,
                lister=context.workspace,
                sync=context.workspace,
                audit=context.audit,
                main_group_email=Email(context.main_group),
                provisioner=context.workspace,
                provision=config,
            )
        finally:
            await pool.close()

    asyncio.run(_go())


@when("the sweep runs with provisioning in dry-run mode")
def step_sweep_with_provisioning_dry(context):
    _ensure_context(context)
    config = ProvisionConfig(
        domain="example.net",
        main_group_name="Everyone",
        mass_creation_tripwire=getattr(context, "tripwire", 10),
    )
    context.audit = RecordingAuditSink()
    # The real dry-run provisioner: real existence reads, recorded (not executed)
    # creates. The DB must be untouched regardless of what it records.
    provisioner = RecordingProvisioner(context.workspace)

    async def _go():
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        try:
            context.report = await run_sweep(
                pool,
                source=None,
                lister=context.workspace,
                sync=context.workspace,
                audit=context.audit,
                main_group_email=Email(context.main_group),
                provisioner=provisioner,
                provision=config,
                dry_run=True,
            )
        finally:
            await pool.close()

    asyncio.run(_go())


@then('the group "{email:S}" exists')
def step_group_exists(context, email):
    assert email in context.workspace.groups


@then('the body "{name}" is still unlinked')
def step_body_still_unlinked(context, name):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "SELECT leader_google_group_email, member_google_group_email "
            "FROM leadership_bodies WHERE name = %s",
            (name,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] is None and row[1] is None


@then("the group-provisioning bootstrap marker is still unset")
def step_bootstrap_still_unset(context):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "SELECT bootstrapped_group_provisioning FROM bootstrap_state"
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] is False


@then('the body "{name}" is linked to that leaders group')
def step_body_linked(context, name):
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        cursor = conn.execute(
            "SELECT leader_google_group_email FROM leadership_bodies WHERE name = %s",
            (name,),
        )
        row = cursor.fetchone()
    assert row is not None and row[0] is not None


@then("the provisioning report created {count:d} groups on the last run")
def step_report_created(context, count):
    assert context.report.provision.created == count


@then("the provisioning report warned of divergence")
def step_report_diverged(context):
    assert context.report.provision.diverged > 0


@then("the provisioning report refused the creation")
def step_report_refused(context):
    assert context.report.provision.refused_over_cap > 0
