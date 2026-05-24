from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import logging
import os
import json

from alive_progress import alive_bar
from cyclopts import App
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from rich import print as rprint
from rich.rule import Rule
from tenacity import retry, retry_if_exception, stop_after_delay, wait_fixed


if TYPE_CHECKING:
    from googleapiclient._apis.admin.directory_v1 import DirectoryResource, Group
    from googleapiclient._apis.cloudidentity.v1 import CloudIdentityResource, Group as CiGroup
    from googleapiclient._apis.groupssettings.v1 import GroupssettingsResource, Groups as SettingsGroup

one_shot_app = App(name="one-shot", help="Throwaway commands for development and manual testing.")

TEST_GROUP_EMAIL = "i-made-a-test-group@portlanddsa.org"
TEST_GROUP_NAME = "A stupid Script Test Group"
TEST_GROUP_DESCRIPTION = "A stupid test security group"
TEST_MEMBER_EMAIL = "info@portlanddsa.org"

TEST_SETTINGS: SettingsGroup = {
                "whoCanJoin": "INVITED_CAN_JOIN",           # only invited users
                "whoCanViewMembership": "ALL_OWNERS_CAN_VIEW",
                "whoCanViewGroup": "ALL_OWNERS_CAN_VIEW",   
                "whoCanInvite": "ALL_OWNERS_CAN_INVITE",
                "whoCanAdd": "ALL_OWNERS_CAN_ADD",
                "whoCanPostMessage": "ALL_OWNERS_CAN_POST",
                "whoCanLeaveGroup": "NONE_CAN_LEAVE",       # must be removed by owner
                "whoCanContactOwner": "ALL_OWNERS_CAN_CONTACT",
                "allowExternalMembers": "false",
                "allowWebPosting": "false",
                "showInGroupDirectory": "false",
                "includeInGlobalAddressList": "false",
                "membersCanPostAsTheGroup": "false",
                "messageModerationLevel": "MODERATE_ALL_MESSAGES",
                "spamModerationLevel": "REJECT",
            }

TEST_SECURITY_LABEL: CiGroup = {
                "labels": {
                    "cloudidentity.googleapis.com/groups.discussion_forum": "",
                    "cloudidentity.googleapis.com/groups.security": "",
                }
            }

logger = logging.getLogger(__name__)

def get_credentials() -> Credentials:
    if "CREDENTIALS_JSON" in os.environ:
        specification = json.loads(os.environ["CREDENTIALS_JSON"])
        return Credentials.from_service_account_info(
                info=specification,
                scopes=[
                    "https://www.googleapis.com/auth/admin.directory.group",
                    "https://www.googleapis.com/auth/cloud-identity.groups",
                    "https://www.googleapis.com/auth/apps.groups.settings",
                ],
            ).with_subject("info@portlanddsa.org")
    elif "CREDENTIALS_PATH" in os.environ:
        return Credentials.from_service_account_file(
                filename=os.environ["CREDENTIALS_PATH"],
                scopes=[
                    "https://www.googleapis.com/auth/admin.directory.group",
                    "https://www.googleapis.com/auth/cloud-identity.groups",
                    "https://www.googleapis.com/auth/apps.groups.settings",
                ],
            ).with_subject("info@portlanddsa.org")
    else:
        raise EnvironmentError("Please set either CREDENTIALS_JSON or CREDENTIALS_PATH. If both are set, CREDENTIALS_JSON is preferred")

class GroupSettings:
    @property
    def group_info(self) -> Group:
        return {
            "email": self.email,
            "name": self.name,
            "description": self.description
        }
        
    @property
    def cloud_identity_name(self) -> str:
        if self.id is None:
            raise ValueError("Please set the 'id' attribute after creating the group because the Cloud Identity API needs it")
        return f"groups/{self.id}"
    
    def __init__(self, 
                 email: str = TEST_GROUP_EMAIL,
                 name: str = TEST_GROUP_NAME,
                 description: str = TEST_GROUP_DESCRIPTION,
                 settings_group: Optional[SettingsGroup] = None, 
                 cloud_identity_group: Optional[CiGroup] = None
                ):
        if settings_group is None:
            settings_group = TEST_SETTINGS
            
        if cloud_identity_group is None:
            cloud_identity_group = TEST_SECURITY_LABEL
            
        self.settings_group = settings_group
        self.cloud_identity_group = cloud_identity_group
        
        self.email = email
        self.name = name
        self.description = description
        
        self.id: Optional[str] = None

def _create_group(settings: GroupSettings, admin_service: DirectoryResource, ci_service: CloudIdentityResource, settings_resource: GroupssettingsResource) -> Group:
    group: Group = admin_service.groups().insert(
            body=settings.group_info
        ).execute()

    assert("id" in group)
    settings.id = group["id"]
    
    _await_creation(settings, ci_service, settings_resource)
    
    return group

def _delete_existing(settings: GroupSettings, admin_service: DirectoryResource):
    try:
        admin_service.groups().get(groupKey=settings.email).execute()
        # It exists, no exception — delete it so the rest can run.
        # This handles the case where the program crashed after group creation.
        admin_service.groups().delete(groupKey=settings.email).execute()
        
        _await_deletion(settings, admin_service)
    except HttpError as e:
        if e.status_code == 404:
            logger.info(f"{settings.email} not found, we're good to go")
        else:
            raise

class _GroupStillExists(Exception):
    pass

@retry(retry=retry_if_exception(lambda e: (isinstance(e,HttpError) and e.status_code != 404) or isinstance(e,_GroupStillExists)), wait=wait_fixed(2), stop=stop_after_delay(60))
def _await_deletion(settings: GroupSettings, admin_service: DirectoryResource):
    try:
        admin_service.groups().get(groupKey=settings.email).execute()
        raise _GroupStillExists()
    except HttpError as e:
        if e.status_code == 404:
            pass
        else:
            raise

@retry(retry=retry_if_exception(lambda e: isinstance(e,HttpError) and e.status_code == 404), wait=wait_fixed(2), stop=stop_after_delay(60))
def _await_creation(settings: GroupSettings, ci_service: CloudIdentityResource, settings_service: GroupssettingsResource):
    """
    Even though the group is immediately consistent with the admin directory API, it might not be with the settings
    and Cloud Identity APIs yet, so wait for that before continuing to avoid exceptions and sadness
    """
    settings_service.groups().get(groupUniqueId=settings.email).execute()
    ci_service.groups().get(name=settings.cloud_identity_name).execute()

@retry(retry=retry_if_exception(lambda e: isinstance(e,AssertionError)), wait=wait_fixed(2), stop=stop_after_delay(60))
def _await_settings(settings: GroupSettings, settings_service: GroupssettingsResource, ci_service: CloudIdentityResource):
    remote_settings = settings_service.groups().get(groupUniqueId=settings.email).execute()
    assert all(remote_settings.get(k) == v for k, v in settings.settings_group.items())

    remote_labels = ci_service.groups().get(name=settings.cloud_identity_name).execute()
    assert "labels" in settings.cloud_identity_group
    assert remote_labels.get("labels") == settings.cloud_identity_group["labels"] 
    
@retry(retry=retry_if_exception(lambda e: isinstance(e,AssertionError)), wait=wait_fixed(2), stop=stop_after_delay(60))
def _await_member_add(settings: GroupSettings, admin_service: DirectoryResource, member_email: str):
    result = admin_service.members().hasMember(groupKey=settings.email, memberKey=member_email).execute()
    assert "isMember" in result and result["isMember"]

@one_shot_app.command(name="test-create-group")
def one_shot_test_create_group(delete_at_end: bool = True) -> None:
    """Create a test security group, configure its settings, and add a test member."""

    settings = GroupSettings()

    with alive_bar(10, title="test-create-group") as bar:
        bar.text("Authenticating")
        creds = get_credentials()
        creds.refresh(Request())
        admin_service: DirectoryResource = discovery.build("admin", "directory_v1", credentials=creds)
        identity_service: CloudIdentityResource = discovery.build("cloudidentity", "v1", credentials=creds)
        settings_service: GroupssettingsResource = discovery.build("groupssettings", "v1", credentials=creds)
        bar()

        bar.text("Checking for existing group")
        _delete_existing(settings, admin_service)
        bar()

        bar.text("Creating new group")
        _create_group(settings, admin_service, identity_service, settings_service)
        bar()

        bar.text("Adding security group label")
        identity_service.groups().patch(
            name=settings.cloud_identity_name,
            updateMask="labels",
            body=settings.cloud_identity_group,
        ).execute()
        bar()

        bar.text("Changing group settings")
        settings_service.groups().patch(
            groupUniqueId=settings.email,
            body=settings.settings_group,
        ).execute()
        bar()
        
        # While it doesn't matter in the test, we should verify settings
        # before adding a member to prevent, IDK, am unlikely 1 second window for an exploit
        # with being added before the group is restricted
        bar.text("Verifying settings are consistent")
        _await_settings(settings, settings_service, identity_service)
        bar()
        
        bar.text("Adding members")
        admin_service.members().insert(
            groupKey=settings.email,
            body={"email": TEST_MEMBER_EMAIL, "role": "MEMBER"},
        ).execute()
        bar()
        
        bar.text("Verifying member addition is consistent")
        _await_member_add(settings, admin_service, TEST_MEMBER_EMAIL)
        bar()
        
        # Technically redundant but good for testing purposes
        bar.text("Collecting group info")
        members = admin_service.members().list(groupKey=settings.email).execute().get("members", [])
        remote_settings = settings_service.groups().get(groupUniqueId=settings.email).execute()
        ci_group = identity_service.groups().get(name=settings.cloud_identity_name).execute()
        bar()

        bar.text("Deleting group")
        if delete_at_end:
            admin_service.groups().delete(groupKey=settings.email).execute()
            _await_deletion(settings, admin_service)
        else:
            logger.info("Actually, not deleting by request")
        bar()

    rprint(Rule("Members"))
    rprint(members)

    rprint(Rule("Settings"))
    rprint(remote_settings)

    rprint(Rule("Labels"))
    rprint(ci_group.get("labels", {}))

