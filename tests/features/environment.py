"""behave hooks: skip live-credentialed scenarios, and the lazy `@db` Postgres rig."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from tests.support.pg import Rig, start

# httpx emits INFO-level request logs by default; silence them so the behave
# output stays clean for offline/contract scenarios.
logging.getLogger("httpx").setLevel(logging.WARNING)

#: The shared `@db` Postgres rig, memoized at module scope rather than on
#: `context`: behave discards an attribute set in `before_scenario` when it
#: pops that scenario's own context layer at `after_scenario`, so storing the
#: container there would restart it on every `@db` scenario instead of once.
_rig: Rig | None = None


def _has_google_creds() -> bool:
    return any(
        os.environ.get(name)
        for name in ("CREDENTIALS_JSON", "CREDENTIALS_FILE", "CREDENTIALS_PATH")
    )


def _has_st_token() -> bool:
    return bool(os.environ.get("SOLIDARITY_TECH_TOKEN"))


def before_all(context) -> None:
    # psycopg's async pool cannot run under Windows' default ProactorEventLoop;
    # it needs a selector loop, set once before any `@db` scenario opens a pool.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def before_scenario(context, scenario) -> None:
    """Skip a `@live` scenario unless its credentials are in the environment.

    Start the shared Postgres rig the first time a `@db` scenario runs anywhere
    in the run, reusing it for every later `@db` scenario; truncate the domain
    tables first so each one starts from an empty database. Offline suites
    (smoke, google_group_offline, contract) never touch this and so never need
    a container.
    """
    global _rig

    if "live" in scenario.effective_tags and not (
        _has_google_creds() or _has_st_token()
    ):
        scenario.skip(
            "requires live credentials (CREDENTIALS_* or SOLIDARITY_TECH_TOKEN)"
        )

    if "db" in scenario.effective_tags:
        if _rig is None:
            _rig = start()
        else:
            _rig.truncate()
        context.db = _rig.db


def after_scenario(context, scenario) -> None:
    """Tear down per-scenario patches: stop any respx router and restore the
    monkeypatched Google service builder."""
    router = getattr(context, "router", None)
    if router is not None:
        router.stop(clear=False, reset=False)

    orig_build = getattr(context, "orig_build", None)
    if orig_build is not None:
        import rosadmin.google_group as gg

        gg.build_services = orig_build


def after_all(context) -> None:
    """Stop the shared Postgres rig if any `@db` scenario started it."""
    if _rig is not None:
        _rig.stop()
