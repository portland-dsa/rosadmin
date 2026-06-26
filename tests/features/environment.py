"""behave hooks: skip live-credentialed scenarios when no credentials are present."""

from __future__ import annotations

import logging
import os

# httpx emits INFO-level request logs by default; silence them so the behave
# output stays clean for offline/contract scenarios.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _has_google_creds() -> bool:
    return any(
        os.environ.get(name)
        for name in ("CREDENTIALS_JSON", "CREDENTIALS_FILE", "CREDENTIALS_PATH")
    )


def _has_st_token() -> bool:
    return bool(os.environ.get("SOLIDARITY_TECH_TOKEN"))


def before_scenario(context, scenario) -> None:
    """Skip a `@live` scenario unless the credentials it needs are in the environment."""
    if "live" in scenario.effective_tags and not (
        _has_google_creds() or _has_st_token()
    ):
        scenario.skip(
            "requires live credentials (CREDENTIALS_* or SOLIDARITY_TECH_TOKEN)"
        )


def after_scenario(context, scenario) -> None:
    """Tear down per-scenario patches: stop any respx router and restore the
    monkeypatched Google service builder."""
    router = getattr(context, "router", None)
    if router is not None:
        router.stop(clear=False, reset=False)

    orig_build = getattr(context, "orig_build", None)
    if orig_build is not None:
        import rosadmin.google_group as gg

        gg._build_services = orig_build
