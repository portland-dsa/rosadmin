"""The Google boundary a write endpoint calls through: two skip gates, then
either the real three-API mirror or a dry-run stand-in.

`GroupSync` is the port; `GoogleGroupSync` is the real adapter and
`DryRunGroupSync` a same-shaped stand-in that never calls Google.  Both gate on
the same two conditions - an unlinked body (`group_email is None`) and an
unusable `@example.com` address - through the shared `_skip_gate` helper below,
so the two implementations cannot drift apart on what gets skipped.
`RecordingGroupSync` wraps either one (default: `DryRunGroupSync`) and keeps a
list of what it saw, which is how a test or a future reconcile sweep observes
outcomes without touching Google.

`group_sync_from_env` is the boot-time selector: dry-run when
`ROSADMIN_GOOGLE_DRY_RUN=1`, otherwise the real sync - which demands
credentials up front, so a misconfigured box fails at startup rather than on
the first write.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from googleapiclient import discovery
from googleapiclient.errors import HttpError

from rosadmin.auth import get_credentials
from rosadmin.google_group import (
    SECURE_SETTINGS,
    SECURITY_LABEL,
    GoogleGroup,
    GroupMemberEntry,
    build_services,
    list_group_members,
)
from rosadmin.membership.source import Email, sync_email

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials
    from googleapiclient._apis.admin.directory_v1 import DirectoryResource
    from googleapiclient._apis.cloudidentity.v1 import CloudIdentityResource
    from googleapiclient._apis.groupssettings.v1 import GroupssettingsResource

    #: A quoted (forward-reference) type alias, not a plain assignment: the
    #: three Google resource types are @type_check_only and pyright rejects
    #: binding them to a name anywhere but an annotation position.
    _Services: TypeAlias = (
        "tuple[DirectoryResource, CloudIdentityResource, GroupssettingsResource]"
    )

logger = logging.getLogger(__name__)

#: The unusable-address suffix marking a record with no real Google-capable
#: email. Never mirrored; `records` also consults it when resolving a sync
#: target, so an example-primary record cannot dodge the gate via an alternate.
EXAMPLE_DOMAIN = "@example.com"


@runtime_checkable
class SyncAddresses(Protocol):
    """A row carrying the two address columns `sync_target` consults.

    Read-only properties so frozen row dataclasses satisfy it structurally.
    """

    @property
    def email(self) -> str: ...
    @property
    def alternate_email(self) -> str | None: ...


def sync_target(row: SyncAddresses) -> Email:
    """The address a Google membership operation targets, per `sync_email`.

    A record whose primary is an example-domain address is an unusable or
    fabricated record wholesale, so it never redirects to an alternate: the
    skip gate must see the example primary and skip, not a plausible gmail
    alternate it would happily deliver to. Without this, a fabricated test
    persona carrying a made-up gmail alternate would sail through the gate
    and really be invited on a live tenant.
    """
    primary = Email(row.email)
    if primary.lower().endswith(EXAMPLE_DOMAIN):
        return primary
    alternate = Email(row.alternate_email) if row.alternate_email else None
    return sync_email(primary, alternate)


class SyncOutcome(Enum):
    """What happened when a `GroupSync` operation was asked to run."""

    Applied = "applied"
    SkippedUnlinked = "skipped_unlinked"
    SkippedExampleEmail = "skipped_example_email"
    SkippedDryRun = "skipped_dry_run"
    AlreadyConverged = "already_converged"
    Failed = "failed"


class GroupSync(Protocol):
    """The port a write endpoint calls through to mirror a membership change."""

    async def add(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome: ...

    async def remove(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome: ...


class GroupLister(Protocol):
    """The read side of the Google boundary: one group's remote member list."""

    async def list(self, group_email: Email) -> list[GroupMemberEntry]: ...


class GoogleGroupLister:
    """The real lister. Builds only the Admin Directory client per call - the
    one client a membership listing touches."""

    def __init__(self, creds: Credentials) -> None:
        self._creds = creds

    async def list(self, group_email: Email) -> list[GroupMemberEntry]:
        admin = await asyncio.to_thread(
            discovery.build,
            "admin",
            "directory_v1",
            credentials=self._creds,
            cache_discovery=False,
        )
        return await asyncio.to_thread(list_group_members, admin, group_email)


def group_lister_from_env(env: Mapping[str, str]) -> GroupLister | None:
    """The lister a sweep uses, or None when the environment forbids Google reads.

    `ROSADMIN_GOOGLE_DRY_RUN=1` means no Workspace calls of any kind, reads
    included - the caller degrades to a desired-state-only report. Otherwise
    credentials are demanded up front, same as `group_sync_from_env`.
    """
    if env.get("ROSADMIN_GOOGLE_DRY_RUN") == "1":
        return None
    subject = _subject_from_env(env)
    try:
        creds = get_credentials(subject)
    except EnvironmentError as error:
        raise RuntimeError(
            f"google lister credentials are not configured: {error}"
        ) from error
    return GoogleGroupLister(creds)


def _skip_gate(
    group_email: Email | None, member_email: Email, *, expect_example_emails: bool
) -> SyncOutcome | Email:
    """The two skip gates, shared by the real and dry-run implementations.

    Returns the outcome to short-circuit on, or the (now known non-None)
    group address when neither gate fires - handing back the narrowed value
    is what lets a caller proceed without re-checking for `None`. Order is
    fixed: an unlinked body (`group_email is None`) is checked before the
    address itself, so a body with no remote group never even looks at the
    member's address.

    The example-address log line names the domain fact, never the address -
    `expect_example_emails` only tunes WARNING (production: an unusable
    address on a real record) down to INFO (staging: expected test personas).
    """
    if group_email is None:
        return SyncOutcome.SkippedUnlinked
    if member_email.lower().endswith(EXAMPLE_DOMAIN):
        level = logging.INFO if expect_example_emails else logging.WARNING
        logger.log(
            level,
            "google sync skipped: member email ends in %s (unusable address on record)",
            EXAMPLE_DOMAIN,
        )
        return SyncOutcome.SkippedExampleEmail
    return group_email


class GoogleGroupSync:
    """The real mirror: skip gates first, then a direct membership call.

    `expect_example_emails` only tunes the log level of the example-address
    skip - staging expects them (test personas), production warns (a member
    with an unusable address on record). Behavior is identical either way.

    The three Google service clients are built fresh per operation, not cached:
    the underlying httplib2 transport is not thread-safe, and concurrent
    background mirror tasks each run in their own thread. Building is cheap -
    the discovery documents are bundled with the client library, so no network
    round trip is involved - and a per-call triple means no two threads ever
    share a transport. `add_member`/`remove_member` only ever touch the Admin
    Directory client and the group's own email, so there is no need to hydrate
    a full `GoogleGroup` (which would also fetch Groups Settings and Cloud
    Identity) per call either.
    """

    def __init__(self, creds: Credentials, *, expect_example_emails: bool) -> None:
        self._creds = creds
        self._expect_example = expect_example_emails

    async def _services(self) -> _Services:
        return await asyncio.to_thread(build_services, self._creds)

    async def add(self, group_email: Email | None, member_email: Email) -> SyncOutcome:
        return await self._apply("add", group_email, member_email)

    async def remove(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        return await self._apply("remove", group_email, member_email)

    async def _apply(
        self, op: str, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        gated = _skip_gate(
            group_email, member_email, expect_example_emails=self._expect_example
        )
        if isinstance(gated, SyncOutcome):
            return gated
        try:
            admin, identity, settings = await self._services()
            group = GoogleGroup(
                email=gated,
                name="",
                description="",
                settings_group=SECURE_SETTINGS,
                cloud_identity_group=SECURITY_LABEL,
                admin=admin,
                identity=identity,
                settings=settings,
            )
            if op == "add":
                await group.add_member(member_email)
            else:
                await group.remove_member(member_email)
        except HttpError as error:
            converged_status = 409 if op == "add" else 404
            if error.status_code == converged_status:
                # The mutation came back saying the desired state already
                # holds: an add on an existing member, or a remove on a
                # non-member. Not a failure -
                # the db and Google have simply drifted apart and are already
                # converged, which is worth one loud line naming the group
                # (never the member address, which a remove's own error may
                # carry as a path segment).
                logger.warning(
                    "google sync %s on %s: db/google drift detected, "
                    "mirror already converged",
                    op,
                    gated,
                )
                return SyncOutcome.AlreadyConverged
            # Only the status code: rendering the HttpError itself would embed
            # the request URI, which for a remove carries the member address
            # as a path segment.
            logger.error(
                "google sync %s on %s failed: status %s", op, gated, error.status_code
            )
            return SyncOutcome.Failed
        return SyncOutcome.Applied


class DryRunGroupSync:
    """A same-shaped stand-in for `GoogleGroupSync` that never calls Google.

    Runs the same two skip gates - so local dev and dry-run deployments still
    exercise the real gate logic - then logs what it would have done at INFO
    and reports `SkippedDryRun`.
    """

    def __init__(self, *, expect_example_emails: bool) -> None:
        self._expect_example = expect_example_emails

    async def add(self, group_email: Email | None, member_email: Email) -> SyncOutcome:
        return await self._dry_run("add", group_email, member_email)

    async def remove(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        return await self._dry_run("remove", group_email, member_email)

    async def _dry_run(
        self, op: str, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        gated = _skip_gate(
            group_email, member_email, expect_example_emails=self._expect_example
        )
        if isinstance(gated, SyncOutcome):
            return gated
        # The group only - even at dry-run INFO, a member address never
        # reaches a log line.
        logger.info("dry-run: would %s a member on %s", op, gated)
        return SyncOutcome.SkippedDryRun


#: One recorded call: which operation, on which group, for which member, with
#: what outcome. A plain tuple - `RecordingGroupSync` exists only to observe an
#: inner `GroupSync`, not to add its own vocabulary.
RecordedSync = tuple[str, Email | None, Email, SyncOutcome]


class RecordingGroupSync:
    """Wraps an inner `GroupSync` (default `DryRunGroupSync`) and records every call.

    Delegates every `add`/`remove` to the inner sync and appends
    `(op, group_email, member_email, outcome)` to `recorded`. Because the skip
    gates live inside the inner sync and run before any dry-run or real
    short-circuit, wrapping the default `DryRunGroupSync` still exercises the
    real gate logic - a test wired with the default sees real outcomes, not a
    fake that replaces the logic under test.
    """

    def __init__(self, inner: GroupSync | None = None) -> None:
        self._inner: GroupSync = (
            inner if inner is not None else DryRunGroupSync(expect_example_emails=False)
        )
        self.recorded: list[RecordedSync] = []

    async def add(self, group_email: Email | None, member_email: Email) -> SyncOutcome:
        outcome = await self._inner.add(group_email, member_email)
        self.recorded.append(("add", group_email, member_email, outcome))
        return outcome

    async def remove(
        self, group_email: Email | None, member_email: Email
    ) -> SyncOutcome:
        outcome = await self._inner.remove(group_email, member_email)
        self.recorded.append(("remove", group_email, member_email, outcome))
        return outcome


def _subject_from_env(env: Mapping[str, str]) -> str:
    subject = env.get("ROSADMIN_GOOGLE_SUBJECT")
    if not subject:
        raise RuntimeError(
            "ROSADMIN_GOOGLE_SUBJECT is required when Google sync is not in dry-run mode"
        )
    return subject


def group_sync_from_env(env: Mapping[str, str]) -> GroupSync:
    """Select and build the `GroupSync` a running service uses.

    Dry-run when `ROSADMIN_GOOGLE_DRY_RUN=1` - logs one loud startup WARNING so
    the mode is never silently on. Otherwise the real sync, which demands the
    impersonation subject and Workspace credentials up front: a misconfigured
    box fails fast at boot rather than on the first write.
    """
    expect = env.get("ROSADMIN_EXPECT_EXAMPLE_EMAILS") == "1"
    if env.get("ROSADMIN_GOOGLE_DRY_RUN") == "1":
        logger.warning(
            "google sync is in DRY-RUN mode: no Workspace calls will be made"
        )
        return DryRunGroupSync(expect_example_emails=expect)
    subject = _subject_from_env(env)
    try:
        creds = get_credentials(subject)
    except EnvironmentError as error:
        raise RuntimeError(
            f"google sync credentials are not configured: {error}"
        ) from error
    return GoogleGroupSync(creds, expect_example_emails=expect)
