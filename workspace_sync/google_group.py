from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from googleapiclient import discovery
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_delay, wait_fixed

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials
    from googleapiclient._apis.admin.directory_v1 import DirectoryResource, Group
    from googleapiclient._apis.cloudidentity.v1 import (
        CloudIdentityResource,
        Group as CiGroup,
    )
    from googleapiclient._apis.groupssettings.v1 import (
        GroupssettingsResource,
        Groups as SettingsGroup,
    )

logger = logging.getLogger(__name__)

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

SECURITY_LABEL: CiGroup = {
    "labels": {
        "cloudidentity.googleapis.com/groups.discussion_forum": "",
        "cloudidentity.googleapis.com/groups.security": "",
    }
}


class _GroupStillExists(Exception):
    pass


def _build_services(
    creds: Credentials,
) -> tuple[DirectoryResource, CloudIdentityResource, GroupssettingsResource]:
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


class GoogleGroup:
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
        return {"email": self.email, "name": self.name, "description": self.description}

    @property
    def cloud_identity_name(self) -> str:
        if self.id is None:
            raise ValueError("id not set — has the group been created yet?")
        return f"groups/{self.id}"

    # ------------------------------------------------------------------
    # Tenacity retry helpers (sync; run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, HttpError) and e.status_code == 404
        ),
        wait=wait_fixed(2),
        stop=stop_after_delay(60),
    )
    def _await_creation(self) -> None:
        self._settings.groups().get(groupUniqueId=self.email).execute()
        self._identity.groups().get(name=self.cloud_identity_name).execute()

    @retry(
        retry=retry_if_exception(
            lambda e: (isinstance(e, HttpError) and e.status_code != 404)
            or isinstance(e, _GroupStillExists)
        ),
        wait=wait_fixed(2),
        stop=stop_after_delay(60),
    )
    def _await_deletion(self) -> None:
        try:
            self._admin.groups().get(groupKey=self.email).execute()
            raise _GroupStillExists()
        except HttpError as e:
            if e.status_code == 404:
                pass
            else:
                raise

    @retry(
        retry=retry_if_exception(lambda e: isinstance(e, AssertionError)),
        wait=wait_fixed(2),
        stop=stop_after_delay(60),
    )
    def _await_settings(self) -> None:
        remote = self._settings.groups().get(groupUniqueId=self.email).execute()
        assert all(remote.get(k) == v for k, v in self.settings_group.items())

        remote_ci: CiGroup = (
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        )
        assert "labels" in self.cloud_identity_group
        assert remote_ci.get("labels") == self.cloud_identity_group["labels"]

    @retry(
        retry=retry_if_exception(lambda e: isinstance(e, AssertionError)),
        wait=wait_fixed(2),
        stop=stop_after_delay(60),
    )
    def _await_member_add(self, member_email: str) -> None:
        result = (
            self._admin.members()
            .hasMember(groupKey=self.email, memberKey=member_email)
            .execute()
        )
        assert "isMember" in result and result["isMember"]

    # ------------------------------------------------------------------
    # Raw sync operations
    # ------------------------------------------------------------------

    def _raw_create(self) -> None:
        group: Group = self._admin.groups().insert(body=self.group_info).execute()
        assert "id" in group
        self.id = group["id"]
        self._await_creation()

    def _raw_configure(self) -> None:
        self._identity.groups().patch(
            name=self.cloud_identity_name,
            updateMask="labels",
            body=self.cloud_identity_group,
        ).execute()
        self._settings.groups().patch(
            groupUniqueId=self.email,
            body=self.settings_group,
        ).execute()
        self._await_settings()

    def _raw_add_member(self, email: str, role: str) -> None:
        self._admin.members().insert(
            groupKey=self.email,
            body={"email": email, "role": role},
        ).execute()
        self._await_member_add(email)

    def _raw_delete(self, missing_ok: bool) -> None:
        try:
            self._admin.groups().get(groupKey=self.email).execute()
            self._admin.groups().delete(groupKey=self.email).execute()
            self._await_deletion()
        except HttpError as e:
            if missing_ok and e.status_code == 404:
                logger.info("%s not found, nothing to delete", self.email)
            else:
                raise

    def _raw_list_members(self) -> list:
        return (
            self._admin.members().list(groupKey=self.email).execute().get("members", [])
        )

    def _raw_get_settings(self) -> SettingsGroup:
        return self._settings.groups().get(groupUniqueId=self.email).execute()

    def _raw_get_labels(self) -> dict:
        ci: CiGroup = (
            self._identity.groups().get(name=self.cloud_identity_name).execute()
        )
        return ci.get("labels", {})  # type: ignore[return-value]

    def _raw_fetch(self) -> None:
        """Populate this object's state from the remote group."""
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
        def _init() -> GoogleGroup:
            admin, identity, settings_svc = _build_services(creds)
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

    async def configure(self) -> None:
        await asyncio.to_thread(self._raw_configure)

    async def add_member(self, email: str, role: str = "MEMBER") -> None:
        await asyncio.to_thread(self._raw_add_member, email, role)

    async def list_members(self) -> list:
        return await asyncio.to_thread(self._raw_list_members)

    async def get_settings(self) -> SettingsGroup:
        return await asyncio.to_thread(self._raw_get_settings)

    async def get_labels(self) -> dict:
        return await asyncio.to_thread(self._raw_get_labels)

    async def delete(self, missing_ok: bool = False) -> None:
        await asyncio.to_thread(self._raw_delete, missing_ok)


class GoogleGroupBuilder:
    def __init__(self) -> None:
        self._email: Optional[str] = None
        self._name: Optional[str] = None
        self._description: Optional[str] = None
        self._settings_group: Optional[SettingsGroup] = None
        self._cloud_identity_group: Optional[CiGroup] = None
        self._replace_if_exists: bool = False

    def email(self, email: str) -> GoogleGroupBuilder:
        self._email = email
        return self

    def name(self, name: str) -> GoogleGroupBuilder:
        self._name = name
        return self

    def description(self, description: str) -> GoogleGroupBuilder:
        self._description = description
        return self

    def settings(self, settings: SettingsGroup) -> GoogleGroupBuilder:
        self._settings_group = settings
        return self

    def label(self, label: CiGroup) -> GoogleGroupBuilder:
        self._cloud_identity_group = label
        return self

    def secure_defaults(self) -> GoogleGroupBuilder:
        self._settings_group = SECURE_SETTINGS
        self._cloud_identity_group = SECURITY_LABEL
        return self

    def replace_if_exists(self, value: bool = True) -> GoogleGroupBuilder:
        self._replace_if_exists = value
        return self

    async def build_remote(self, creds: Credentials) -> GoogleGroup:
        assert self._email is not None, "email() is required"
        assert self._name is not None, "name() is required"
        assert self._description is not None, "description() is required"
        assert (
            self._settings_group is not None
        ), "settings() or secure_defaults() is required"
        assert (
            self._cloud_identity_group is not None
        ), "label() or secure_defaults() is required"

        email = self._email
        name = self._name
        description = self._description
        settings_group = self._settings_group
        cloud_identity_group = self._cloud_identity_group
        replace_if_exists = self._replace_if_exists

        def _do_build() -> GoogleGroup:
            admin, identity, settings_svc = _build_services(creds)
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
            if replace_if_exists:
                group._raw_delete(missing_ok=True)
            group._raw_create()
            group._raw_configure()
            return group

        return await asyncio.to_thread(_do_build)
