"""The rosadmin web service: the leader-facing API behind systemd `Type=notify`.

The `create_app` factory assembles the `rosadmin.web` package - contract routes,
problem-details errors, the session dependency, and the Origin guard. The lifespan
turns a passing health check into the unit's readiness signal and pets the watchdog
on a timer, so an active unit is one that answered its own health check.

The development surface (persona stubs, fake-login) and the interactive docs mount
only behind a double gate: the fake-login setting on AND the rosadmin-devtools
package installed. Production carries neither, so they are absent, not merely off.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from rosadmin import systemd_notify
from rosadmin.web.auth import auth_router, origin_guard
from rosadmin.web.problems import install_handlers
from rosadmin.web.routes import api_router
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings, settings_from_env


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _watchdog(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        systemd_notify.notify_watchdog()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Readiness is gated on the health check, so READY=1 means it passed. Explicit
    # check (not assert) so the readiness gate is not elided under `python -O`.
    if (await _healthz())["status"] != "ok":
        raise RuntimeError("startup health check failed")
    systemd_notify.notify_ready()

    interval = systemd_notify.watchdog_interval()
    task = asyncio.create_task(_watchdog(interval)) if interval is not None else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _devtools_active(settings: WebSettings) -> bool:
    """Both halves of the double gate: the flag set AND the package present.

    Production artifacts contain neither, so the dev surface there is not disabled
    - it is GONE, and no configuration mistake can conjure it.
    """
    return (
        settings.fake_login_enabled
        and importlib.util.find_spec("rosadmin_devtools") is not None
    )


def create_app(settings: WebSettings | None = None) -> FastAPI:
    """Assemble the service; the dev surface and docs sit behind the double gate."""
    if settings is None:
        settings = settings_from_env(os.environ)
    devtools = _devtools_active(settings)
    app = FastAPI(
        lifespan=_lifespan,
        docs_url="/api/docs" if devtools else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if devtools else None,
    )
    app.state.settings = settings
    app.state.session_store = InMemorySessionStore()
    install_handlers(app)
    app.middleware("http")(origin_guard)
    app.include_router(api_router)
    app.include_router(auth_router)
    if devtools:
        # Imported inside the gate: the module does not exist in production.
        from rosadmin_devtools import StubDirectory, fake_login_router

        app.state.directory = StubDirectory()
        app.include_router(fake_login_router)
    return app


app = create_app()
