from __future__ import annotations

import json
import os

from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/cloud-identity.groups",
    "https://www.googleapis.com/auth/apps.groups.settings",
]


def get_credentials(subject: str) -> Credentials:
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
