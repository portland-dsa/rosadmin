"""The rosadmin web service: a minimal FastAPI app behind systemd `Type=notify`.

The only route is the health check. The lifespan turns a passing
health check into the unit's readiness signal and pets the watchdog on a timer, so
an active unit is one that answered its own `/healthz` - not merely a process that
started. Docs routes are disabled: this is an internal service, not a public API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from rosadmin import systemd_notify


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _watchdog(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        systemd_notify.notify_watchdog()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Readiness is gated on the health check, so READY=1 means /healthz answered.
    # Explicit check (not assert) so the readiness gate is not elided under `python -O`.
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


def create_app() -> FastAPI:
    app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/healthz")(_healthz)
    return app


app = create_app()
