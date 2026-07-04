"""Web-layer configuration, read once from the environment at startup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class WebSettings:
    """Feature flags the deployment sets; the code never guesses its stage.

    `fake_login_enabled` is half of a double gate: the fake-login route and
    the interactive docs exist only when this flag is set AND the
    rosadmin-devtools package is installed. Production artifacts carry
    neither the flag nor the package, so the dev surface is not merely
    disabled there - it is absent, and a prober sees 404, not 403.
    """

    fake_login_enabled: bool
    allowed_origin: str | None


def settings_from_env(env: Mapping[str, str]) -> WebSettings:
    """`ROSADMIN_FAKE_LOGIN=1` opts in; `ROSADMIN_ORIGIN` pins browser origin."""
    origin = env.get("ROSADMIN_ORIGIN", "")
    return WebSettings(
        fake_login_enabled=env.get("ROSADMIN_FAKE_LOGIN", "") == "1",
        allowed_origin=origin if len(origin) > 0 else None,
    )
