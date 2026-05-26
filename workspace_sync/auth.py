"""
Credential loading for Google Workspace Domain-Wide Delegation (DWD).

The service account key can be supplied as a JSON string (preferred for CI/CD)
or as a file path. This is also the intended future home for secret-manager or
env-driven subject configuration.
"""

from __future__ import annotations

import json
import os

from google.oauth2.service_account import Credentials

#: OAuth scopes for the three Google APIs used by `GoogleGroup`:
#: Admin Directory, Cloud Identity, and Groups Settings.
SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/cloud-identity.groups",
    "https://www.googleapis.com/auth/apps.groups.settings",
]


def get_credentials(subject: str) -> Credentials:
    """Load a DWD-capable service account credential impersonating `subject`.

    Resolves the service account key from environment variables in this order:
    ``CREDENTIALS_JSON`` (raw JSON string) > ``CREDENTIALS_FILE`` >
    ``CREDENTIALS_PATH``. ``subject`` must be an actual Workspace user that the
    service account has been delegated to impersonate — it cannot be the service
    account itself.

    Raises:
        EnvironmentError: if none of the credential env vars are set.
    """
    if "CREDENTIALS_JSON" in os.environ:
        info = json.loads(os.environ["CREDENTIALS_JSON"])
        creds = Credentials.from_service_account_info(info=info, scopes=SCOPES)
    elif key_path := os.environ.get("CREDENTIALS_FILE") or os.environ.get(
        "CREDENTIALS_PATH"
    ):
        creds = Credentials.from_service_account_file(filename=key_path, scopes=SCOPES)
    else:
        raise EnvironmentError(
            "Please set CREDENTIALS_JSON, CREDENTIALS_FILE, or CREDENTIALS_PATH. "
            "If CREDENTIALS_JSON is set it takes precedence."
        )
    return creds.with_subject(subject)
