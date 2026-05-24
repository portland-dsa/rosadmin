from __future__ import annotations

from typing import TYPE_CHECKING, reveal_type

if TYPE_CHECKING:
    from googleapiclient._apis.admin.directory_v1 import DirectoryResource, Group
    from googleapiclient._apis.cloudidentity.v1 import CloudIdentityResource
    from googleapiclient._apis.groupssettings.v1 import GroupssettingsResource

from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient import discovery

creds = service_account.Credentials.from_service_account_file(
    "../test-script/groups-rbam.json",
    scopes=[
            "https://www.googleapis.com/auth/admin.directory.group", 
            "https://www.googleapis.com/auth/cloud-identity.groups",
            'https://www.googleapis.com/auth/apps.groups.settings',
           ],
).with_subject("info@portlanddsa.org")

creds.refresh(Request())

admin_service: DirectoryResource = discovery.build('admin', 'directory_v1', credentials=creds)
identity_service: CloudIdentityResource = discovery.build('cloudidentity', 'v1', credentials=creds)
settings_service: GroupssettingsResource = discovery.build('groupssettings', 'v1', credentials=creds)

groups = admin_service.groups()


new_group: Group = groups.insert(body= {
    'email': 'i-made-a-test-group@portlanddsa.org',
    'name': "A stupid Script Test Group",
    'description': "A stupid test security group"
}).execute()

print(f"Created Group: {new_group}")
group_name = f"groups/{new_group['id']}".format() # type: ignore[typeddict-item]
group_email = new_group["email"] # type: ignore[typeddict-item]

identity_service.groups().patch(
    name=group_name,
    updateMask='labels',
    body={
        'labels': {
            'cloudidentity.googleapis.com/groups.discussion_forum': '',
            'cloudidentity.googleapis.com/groups.security': '',
        }
    }
).execute()

settings_service.groups().patch(
    groupUniqueId=group_email,
    body={
        'whoCanJoin': 'INVITED_CAN_JOIN',           # only invited users
        'whoCanViewMembership': 'ALL_OWNERS_CAN_VIEW',
        'whoCanViewGroup': 'ALL_OWNERS_CAN_VIEW',    # owners only can see content
        'whoCanInvite': 'ALL_OWNERS_CAN_INVITE',
        'whoCanAdd': 'ALL_OWNERS_CAN_ADD',
        'whoCanPostMessage': 'ALL_OWNERS_CAN_POST',
        'whoCanLeaveGroup': 'NONE_CAN_LEAVE',        # must be removed by owner
        'whoCanContactOwner': 'ALL_OWNERS_CAN_CONTACT',
        'allowExternalMembers': 'false',
        'allowWebPosting': 'false',
        'showInGroupDirectory': 'false',
        'includeInGlobalAddressList': 'false',
        'membersCanPostAsTheGroup': 'false',
        'messageModerationLevel': 'MODERATE_ALL_MESSAGES',
        'spamModerationLevel': 'REJECT',
    },
).execute()

admin_service.members().insert(
    groupKey=group_email,
    body={
        'email': "test@example.com",
        'role': "MEMBER"
    }
).execute()

