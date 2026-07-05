"""Reading a secret the way the box delivers it, with a dev fallback.

On the box a secret arrives as a host-key-encrypted systemd credential under
`$CREDENTIALS_DIRECTORY`, readable only by the unit's user and never placed on
the process environment; in local development the same secret is an environment
variable. The audit HMAC key, the migration role's password, and the botonio SSO
bearer all share this shape, so the lookup lives in one place rather than being
retyped at each call site.
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping


def read_credential(env: Mapping[str, str], name: str, env_var: str) -> str | None:
    """The secret `name` from `$CREDENTIALS_DIRECTORY/<name>`, else from `env_var`.

    Returns the whitespace-stripped value, or `None` when neither source carries
    a non-empty one - the required-or-not decision is left to the caller, since a
    bulk command may tolerate a missing optional secret while a targeted one must
    refuse. Stripping makes the two delivery paths agree: a credential file
    written with a trailing newline yields the same value as the same secret set
    inline, so a lookup never forks on whitespace. The result is a secret and is
    never logged.
    """
    creds = env.get("CREDENTIALS_DIRECTORY")
    if creds:
        path = pathlib.Path(creds) / name
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    raw = env.get(env_var)
    if raw is not None and raw.strip():
        return raw.strip()
    return None
