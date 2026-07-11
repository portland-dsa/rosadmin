"""
`GoogleGroup` - a unified async handle to a single Workspace group across
Google's three separate group-management APIs (Admin Directory v1, Cloud
Identity v1, and Groups Settings v1).

Because no single API covers all group-management operations, every group
requires three service clients. This module hides that, along with the
eventual-consistency waits that come after mutations. See `GoogleGroup` for
the central type and `GoogleGroupBuilder` for creating new remote groups.

**Async/sync convention**: public methods that hit the API are ``async`` thin wrappers that
offload blocking work to ``asyncio.to_thread``; the ``_raw_*`` methods do the
actual API calls; ``_poll_until`` and the predicate methods it drives stay
synchronous by design (tenacity doesn't support coroutines the same way). All
three layers run inside threads, never on the event loop directly.

Usage::

    # Create a new group
    group = await (
        GoogleGroupBuilder()
        .email("my-group@example.org")
        .name("My Group")
        .description("...")
        .secure_defaults()
        .build_remote(creds)
    )

    # Hydrate from an existing remote group
    group = await GoogleGroup.from_remote("my-group@example.org", creds)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional, TypeVar

from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
    wait_fixed,
)

from googleapiclient import discovery
from googleapiclient.errors import HttpError

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials
    from googleapiclient._apis.admin.directory_v1 import DirectoryResource, Group
    from googleapiclient._apis.cloudidentity.v1 import (
        CloudIdentityResource,
    )
    from googleapiclient._apis.cloudidentity.v1 import Group as CiGroup
    from googleapiclient._apis.groupssettings.v1 import Groups as SettingsGroup
    from googleapiclient._apis.groupssettings.v1 import (
        GroupssettingsResource,
    )

logger = logging.getLogger(__name__)

#: Secure-by-default Groups Settings payload: invite-only join, owners-only
#: visibility, no external members, all messages moderated. Suitable for an
#: internal security group that should not appear in the global address list.
SECURE_SETTINGS: SettingsGroup = {
    "whoCanJoin": "INVITED_CAN_JOIN",
    "whoCanViewMembership": "ALL_OWNERS_CAN_VIEW",
    "whoCanViewGroup": "ALL_OWNERS_CAN_VIEW",
    "whoCanInvite": "ALL_OWNERS_CAN_INVITE",
    "whoCanAdd": "ALL_OWNERS_CAN_ADD",
    "whoCanPostMessage": "ALL_OWNERS_CAN_POST",
    "whoCanLeaveGroup": "NONE_CAN_LEAVE",
    "whoCanContactOwner": "ALL_OWNERS_CAN_CONTACT",
    "allowExternalMembers": "false",
    "allowWebPosting": "false",
    "showInGroupDirectory": "false",
    "includeInGlobalAddressList": "false",
    "membersCanPostAsTheGroup": "false",
    "messageModerationLevel": "MODERATE_ALL_MESSAGES",
    "spamModerationLevel": "REJECT",
}

#: Cloud Identity labels that designate a group as both a discussion forum and a
#: security group. Google requires empty strings for the label values - the
#: presence of the key is what matters, not the value.
SECURITY_LABEL: CiGroup = {
    "labels": {
        "cloudidentity.googleapis.com/groups.discussion_forum": "",
        "cloudidentity.googleapis.com/groups.security": "",
    }
}


class ExistsBehavior(Enum):
    """What `GoogleGroupBuilder.build_remote` does when the address is taken.

    `Error` (the default) lets the create's 409 propagate; `Replace` deletes the
    existing group and creates fresh; `Link` adopts it - fetches the existing
    group and returns it untouched, configuring nothing.
    """

    Error = "error"
    Replace = "replace"
    Link = "link"


class PropagationTimeout(Exception):
    """A Google-side change was accepted but never became visible in time."""


#: The `reason` values the Admin SDK uses when a 403 means "slow down", not
#: "forbidden" - Google reports most Directory rate limiting as 403 with one
#: of these, rather than as a 429.
_RATE_LIMIT_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
)


def _is_rate_limited_403(error: HttpError) -> bool:
    if error.status_code != 403:
        return False
    # `error_details` is a parsed list for a JSON error body, but an empty
    # string when the body was not parseable - and its entries are dicts for
    # the Admin SDK's envelope but can be plain strings for other shapes.
    details = error.error_details
    if not isinstance(details, list):
        return False
    return any(
        isinstance(detail, dict) and detail.get("reason") in _RATE_LIMIT_REASONS
        for detail in details
    )


def _is_transient_http_error(error: BaseException) -> bool:
    """True for an `HttpError` worth retrying on its own: a server-side 5xx,
    a 429, or a 403 whose reason is one of the Admin SDK's rate-limit spellings -
    any of which Google can return for any of the three APIs during an
    otherwise-healthy operation and which clear on a backoff.

    A 412 (`Precondition Failed` / `conditionNotMet`) is deliberately NOT here.
    A healthy member insert never draws one; when a group is in the state that
    does, every insert to it fails and retrying does not clear it within a run -
    so the only effect of retrying is to stall each add through the whole backoff
    before the identical failure surfaces. It propagates immediately instead,
    failing that add fast and leaving it for the next sweep to re-attempt once
    the group is healthy again. Any other exception, including a genuinely
    forbidden 403, is a real failure and propagates unwrapped immediately."""
    return isinstance(error, HttpError) and (
        error.status_code == 429
        or error.status_code >= 500
        or _is_rate_limited_403(error)
    )


def _poll_until(
    visible: Callable[[], bool], *, interval: float = 2.0, ceiling: float = 60.0
) -> None:
    """Block until `visible` returns True, retrying on the propagation cadence.

    The predicate owns the transient/real-error line: it returns False for the
    lag it expects (a 404 while creation propagates) and lets anything else
    raise. On top of that, this loop itself also retries a transient `HttpError`
    (a 5xx or 429) raised by the predicate, since those can land during any wait
    and are not the predicate's to distinguish from a real failure. Any other
    exception - a non-transient `HttpError` or anything else - still propagates
    unwrapped immediately. Runs synchronously inside `asyncio.to_thread`, like
    everything around it. Raises `PropagationTimeout` when the ceiling passes,
    whether the last attempt returned False or raised a transient error.

    `interval`/`ceiling` exist for tests; production callers never pass them.
    """
    try:
        for attempt in Retrying(
            retry=(
                retry_if_result(lambda done: done is False)
                | retry_if_exception(_is_transient_http_error)
            ),
            wait=wait_fixed(interval),
            stop=stop_after_delay(ceiling),
        ):
            with attempt:
                result = visible()
            outcome = attempt.retry_state.outcome
            if outcome is not None and not outcome.failed:
                attempt.retry_state.set_result(result)
    except RetryError as error:
        raise PropagationTimeout("change accepted but not yet visible") from error


_T = TypeVar("_T")


def _retry_transient(call: Callable[[], _T]) -> _T:
    """Run one blocking API call, waiting out transient failures.

    Quota signals (429, rate-limited 403) and server errors (5xx) get
    exponential backoff with jitter; anything else - a 404, a 409, a 412, a real
    403 - propagates immediately, because those are verdicts, not weather.
    Synchronous on purpose: every caller already runs inside
    ``asyncio.to_thread``.
    """
    retrying = Retrying(
        retry=retry_if_exception(_is_transient_http_error),
        wait=wait_exponential_jitter(initial=1.0, max=30.0),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    return retrying(call)


def build_services(
    creds: Credentials,
) -> tuple[DirectoryResource, CloudIdentityResource, GroupssettingsResource]:
    """Build the three Google API service clients required by `GoogleGroup`.

    Returns a ``(admin, identity, settings)`` tuple: Admin Directory v1, Cloud
    Identity v1, and Groups Settings v1 respectively. ``cache_discovery=False``
    suppresses the spurious ``file_cache`` warning from the discovery client.
    This is a blocking call and should always run inside ``asyncio.to_thread``.

    Public because `GoogleGroupSync` builds the triple once per instance,
    outside of any single `GoogleGroup` handle - a membership mutation does not
    need the full hydration `GoogleGroup.from_remote` performs.
    """
    admin: DirectoryResource = discovery.build(
        "admin", "directory_v1", credentials=creds, cache_discovery=False
    )
    identity: CloudIdentityResource = discovery.build(
        "cloudidentity", "v1", credentials=creds, cache_discovery=False
    )
    settings: GroupssettingsResource = discovery.build(
        "groupssettings", "v1", credentials=creds, cache_discovery=False
    )
    return admin, identity, settings


class GroupsPermissionLevel(Enum):
    """A member's Google Workspace permission over a group.

    This is Google's own membership level - the value the Directory API
    returns for every entry in a group's member list - and is deliberately
    NOT the organization's elected leader/member role, which is a separate
    concept stored on `body_memberships`. The reconcile sweep only ever
    removes plain `Member` entries, so an `Owner`, a `Manager`, or an
    `Other` (any level Google reports that this code does not model) is
    left untouched.
    """

    Owner = "OWNER"
    Manager = "MANAGER"
    Member = "MEMBER"
    Other = "OTHER"

    @classmethod
    def _missing_(cls, value: object) -> GroupsPermissionLevel:
        # A level Google reports that is not one of the three we model maps
        # to Other, which the sweep never removes - an unrecognized level
        # must never be mistaken for a plain member and swept out.
        return cls.Other


@dataclass(frozen=True)
class GroupMemberEntry:
    """One row of a group's remote member list."""

    email: str
    permission_level: GroupsPermissionLevel
    status: str | None
    type: str | None


def list_group_members(admin: DirectoryResource, email: str) -> list[GroupMemberEntry]:
    """Page through `email`'s membership via Admin Directory alone.

    Module-level so a caller that only needs the Admin Directory client (the
    reconcile sweep's lister) can page a group's members without hydrating a
    full `GoogleGroup`, which would also build the Cloud Identity and Groups
    Settings clients neither caller touches.
    """
    entries: list[GroupMemberEntry] = []
    token: str | None = None
    while True:
        request = (
            admin.members().list(groupKey=email, maxResults=200, pageToken=token)
            if token is not None
            else admin.members().list(groupKey=email, maxResults=200)
        )
        page = _retry_transient(request.execute)
        for m in page.get("members", []):
            if "email" not in m or "role" not in m:
                # A CUSTOMER-type member (an admin adding the whole domain)
                # legitimately carries no email; a listing is a bulk read, so
                # skip and note it rather than refusing the whole group.
                logger.warning(
                    "skipping %s member entry with no address in %s",
                    m.get("type", "unknown-type"),
                    email,
                )
                continue
            entries.append(
                GroupMemberEntry(
                    email=m["email"],
                    permission_level=GroupsPermissionLevel(m["role"]),
                    status=m.get("status"),
                    type=m.get("type"),
                )
            )
        token = page.get("nextPageToken")
        if not token:
            return entries


class GoogleGroup:
    """A handle to a single Google Workspace group across all three management APIs.

    Combines Admin Directory v1, Cloud Identity v1, and Groups Settings v1 into
    one object so callers don't have to juggle three clients or know which API
    owns which operation.

    Don't construct this directly. Use `GoogleGroupBuilder.build_remote` to
    create a new remote group, or `GoogleGroup.from_remote` to hydrate an
    existing one.
    """

    def __init__(
        self,
        email: str,
        name: str,
        description: str,
        settings_group: SettingsGroup,
        cloud_identity_group: CiGroup,
        admin: DirectoryResource,
        identity: CloudIdentityResource,
        settings: GroupssettingsResource,
    ):
        self.email = email
        self.name = name
        self.description = description
        self.settings_group = settings_group
        self.cloud_identity_group = cloud_identity_group
        self._admin = admin
        self._identity = identity
        self._settings = settings
        self.id: Optional[str] = None

    @property
    def group_info(self) -> Group:
        """Admin Directory insert/patch body for the basic group fields."""
        return {"email": self.email, "name": self.name, "description": self.description}

    @property
    def cloud_identity_name(self) -> str:
        """The ``groups/{id}`` resource name required by the Cloud Identity API.

        Raises:
            ValueError: if the group hasn't been created or fetched yet (``id`` is None).
        """
        if self.id is None:
            raise ValueError("id not set - has the group been created yet?")
        return f"groups/{self.id}"

    # ------------------------------------------------------------------
    # Propagation predicates (sync; run inside asyncio.to_thread via _poll_until)
    # ------------------------------------------------------------------

    def _creation_visible(self) -> bool:
        """True once a newly-inserted group is visible to Groups Settings and Cloud Identity.

        Admin Directory insert returns immediately, but the other two APIs lag
        and return 404 for an indeterminate period afterward.
        """
        try:
            self._settings.groups().get(groupUniqueId=self.email).execute()
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        except HttpError as e:
            if e.status_code == 404:
                return False
            raise
        return True

    def _group_gone(self) -> bool:
        """True once Admin Directory confirms the group no longer exists."""
        try:
            self._admin.groups().get(groupKey=self.email).execute()
        except HttpError as e:
            if e.status_code == 404:
                return True
            raise
        return False

    def _settings_match(self) -> bool:
        """True once the remote settings and Cloud Identity labels match what was patched."""
        remote = self._settings.groups().get(groupUniqueId=self.email).execute()
        if not all(remote.get(k) == v for k, v in self.settings_group.items()):
            return False
        remote_ci: CiGroup = (
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        )
        return remote_ci.get("labels") == self.cloud_identity_group.get("labels")

    # ------------------------------------------------------------------
    # Raw sync operations
    # ------------------------------------------------------------------

    def _raw_create(self) -> None:
        """Insert the group via Admin Directory, set ``id``, then wait for propagation."""
        group: Group = self._admin.groups().insert(body=self.group_info).execute()
        assert "id" in group
        self.id = group["id"]
        _poll_until(self._creation_visible)

    def _raw_configure(self) -> None:
        """Patch Cloud Identity labels and Groups Settings, then wait for both to propagate."""
        self._identity.groups().patch(
            name=self.cloud_identity_name,
            updateMask="labels",
            body=self.cloud_identity_group,
        ).execute()
        self._settings.groups().patch(
            groupUniqueId=self.email,
            body=self.settings_group,
        ).execute()
        _poll_until(self._settings_match)

    def _raw_add_member(self, email: str, level: GroupsPermissionLevel) -> None:
        """Insert a member via Admin Directory.

        No visibility poll: unlike creation (which spans three APIs), a
        membership mutation is a single-API call whose response is already the
        verdict - a success means the member is in, a 409 means they were
        there all along, anything else raises. A reader that needs the change
        to be *listable* immediately (the live lifecycle test) polls on its
        own read instead.
        """
        request = self._admin.members().insert(
            groupKey=self.email,
            body={"email": email, "role": level.value},
        )
        if logger.isEnabledFor(logging.DEBUG):
            # A universal 412 conditionNotMet on inserts is an If-Match
            # precondition failure, so name whether the request carried one.
            # Header names only, never their values (the bearer token) or the
            # body (the member address).
            headers = request.headers or {}
            logger.debug(
                "member insert headers=%s If-Match=%r",
                sorted(headers),
                headers.get("If-Match"),
            )
        _retry_transient(request.execute)

    def _raw_remove_member(self, email: str) -> None:
        """Delete a member via Admin Directory. Same no-poll contract as adding."""
        request = self._admin.members().delete(groupKey=self.email, memberKey=email)
        _retry_transient(request.execute)

    def _raw_delete(self, missing_ok: bool) -> None:
        """Delete the group via Admin Directory and wait until it's gone.

        If ``missing_ok`` is true, a 404 on the initial fetch is silently ignored.
        """
        try:
            self._admin.groups().get(groupKey=self.email).execute()
            self._admin.groups().delete(groupKey=self.email).execute()
            _poll_until(self._group_gone)
        except HttpError as e:
            if missing_ok and e.status_code == 404:
                logger.info("%s not found, nothing to delete", self.email)
            else:
                raise

    def _raw_list_members(self) -> list[GroupMemberEntry]:
        return list_group_members(self._admin, self.email)

    def _raw_get_settings(self) -> SettingsGroup:
        return self._settings.groups().get(groupUniqueId=self.email).execute()

    def _raw_get_labels(self) -> dict:
        ci: CiGroup = (
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        )
        return ci.get("labels", {})  # type: ignore[return-value]

    def _raw_fetch(self) -> None:
        """Populate ``id``, ``name``, ``description``, ``settings_group``, and
        ``cloud_identity_group`` from the remote group state."""
        g: Group = self._admin.groups().get(groupKey=self.email).execute()
        assert "id" in g
        self.id = g["id"]
        self.name = g.get("name", "")  # type: ignore[assignment]
        self.description = g.get("description", "")  # type: ignore[assignment]
        self.settings_group = self._raw_get_settings()
        ci: CiGroup = (
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        )
        self.cloud_identity_group = ci

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    @classmethod
    async def from_remote(cls, email: str, creds: Credentials) -> GoogleGroup:
        """Construct a `GoogleGroup` from an existing remote group.

        Fetches the group's basic info, settings, and Cloud Identity labels in one
        call. Raises ``HttpError`` (status 404) if the group doesn't exist.
        """

        def _init() -> GoogleGroup:
            admin, identity, settings_svc = build_services(creds)
            group = cls(
                email=email,
                name="",
                description="",
                settings_group=SECURE_SETTINGS,
                cloud_identity_group=SECURITY_LABEL,
                admin=admin,
                identity=identity,
                settings=settings_svc,
            )
            group._raw_fetch()
            return group

        return await asyncio.to_thread(_init)

    async def configure(self, new_settings: SettingsGroup) -> None:
        """Updates the remote Cloud Identity labels and GroupsSettings to the passed in value"""
        self.settings_group = new_settings
        await asyncio.to_thread(self._raw_configure)

    async def add_member(
        self, email: str, level: GroupsPermissionLevel = GroupsPermissionLevel.Member
    ) -> None:
        """Add a member to the group. ``level`` is ``GroupsPermissionLevel.Member`` by
        default; ``GroupsPermissionLevel.Owner`` and ``GroupsPermissionLevel.Manager``
        are also valid."""
        await asyncio.to_thread(self._raw_add_member, email, level)

    async def list_members(self) -> list[GroupMemberEntry]:
        """Return every member across all pages of the remote list."""
        return await asyncio.to_thread(self._raw_list_members)

    async def remove_member(self, email: str) -> None:
        """Remove a member. A 404 (not a member / no such group) raises `HttpError`."""
        await asyncio.to_thread(self._raw_remove_member, email)

    async def get_settings(self) -> SettingsGroup:
        """Retrieves all settings about the group such as and permissions"""
        return await asyncio.to_thread(self._raw_get_settings)

    async def get_labels(self) -> dict:
        """Retrieves the labels of the GoogleGroup, generally this will just be whether it's a Mailing group (always true) and a Security Group."""
        return await asyncio.to_thread(self._raw_get_labels)

    async def delete(self, missing_ok: bool = False) -> None:
        """Delete the remote group. Pass ``missing_ok=True`` to silently ignore a 404."""
        await asyncio.to_thread(self._raw_delete, missing_ok)


class GoogleGroupBuilder:
    """Fluent builder that creates a new remote Workspace group and returns a ready `GoogleGroup`.

    The name ``build_remote`` is deliberate - calling it creates a real group in
    Google Workspace, unlike a plain ``build`` that would only construct a local
    object.

    Usage::

        group = await (
            GoogleGroupBuilder()
            .email("my-group@example.org")
            .name("My Group")
            .description("...")
            .secure_defaults()
            .exists_behavior(ExistsBehavior.Replace)
            .build_remote(creds)
        )
    """

    def __init__(self) -> None:
        self._email: Optional[str] = None
        self._name: Optional[str] = None
        self._description: Optional[str] = None
        self._settings_group: Optional[SettingsGroup] = None
        self._cloud_identity_group: Optional[CiGroup] = None
        self._exists_behavior: ExistsBehavior = ExistsBehavior.Error

    def email(self, email: str) -> GoogleGroupBuilder:
        """Declares the email that will define the Google Group, must be globally unique. Should generally be `@your-domain.org`."""
        self._email = email
        return self

    def name(self, name: str) -> GoogleGroupBuilder:
        """Sets the display name of the Google Group"""
        self._name = name
        return self

    def description(self, description: str) -> GoogleGroupBuilder:
        """The user-friendly description of the group"""
        self._description = description
        return self

    def settings(self, settings: SettingsGroup) -> GoogleGroupBuilder:
        """Sets the general settings for the group, if you want the most restricted options, use ``secure_defaults`` instead."""
        self._settings_group = settings
        return self

    def label(self, label: CiGroup) -> GoogleGroupBuilder:
        """Sets the labels of the Google Group, generally used just to declare it a Security Group. Probably just use ```secure_defaults```"""
        self._cloud_identity_group = label
        return self

    def secure_defaults(self) -> GoogleGroupBuilder:
        """
        A convenience method for ```settings``` which will use ``DEFAULT_SETTINGS`` to create the most restricted group possible, and
        declare it a security group.
        """
        self._settings_group = SECURE_SETTINGS
        self._cloud_identity_group = SECURITY_LABEL
        return self

    def exists_behavior(self, behavior: ExistsBehavior) -> GoogleGroupBuilder:
        """How to treat an address that already resolves to a group: `Error`
        (the default) raises, `Replace` deletes then recreates, `Link` adopts."""
        self._exists_behavior = behavior
        return self

    async def build_remote(self, creds: Credentials) -> GoogleGroup:
        """Create and configure the remote group, returning a ready `GoogleGroup`.

        Asserts that ``email``, ``name``, ``description``, and either
        ``settings``/``label`` or ``secure_defaults`` have been set.
        ``exists_behavior`` governs what happens when the address is already
        taken: `Error` (the default) lets the create's 409 propagate,
        `Replace` deletes the existing group first, and `Link` adopts the
        existing group untouched instead of configuring it.

        Raises:
            AssertionError: if any required builder fields are missing.
        """
        assert self._email is not None, "email() is required"
        assert self._name is not None, "name() is required"
        assert self._description is not None, "description() is required"
        assert self._settings_group is not None, (
            "settings() or secure_defaults() is required"
        )
        assert self._cloud_identity_group is not None, (
            "label() or secure_defaults() is required"
        )

        email = self._email
        name = self._name
        description = self._description
        settings_group = self._settings_group
        cloud_identity_group = self._cloud_identity_group
        behavior = self._exists_behavior

        def _do_build() -> GoogleGroup:
            admin, identity, settings_svc = build_services(creds)
            group = GoogleGroup(
                email=email,
                name=name,
                description=description,
                settings_group=settings_group,
                cloud_identity_group=cloud_identity_group,
                admin=admin,
                identity=identity,
                settings=settings_svc,
            )
            if behavior is ExistsBehavior.Replace:
                group._raw_delete(missing_ok=True)
            try:
                group._raw_create()
            except HttpError as error:
                if behavior is ExistsBehavior.Link and error.status_code == 409:
                    # Adopt: the address is taken; accept the existing group as
                    # truth and hand it back untouched, configuring nothing.
                    group._raw_fetch()
                    return group
                raise
            group._raw_configure()
            return group

        return await asyncio.to_thread(_do_build)
