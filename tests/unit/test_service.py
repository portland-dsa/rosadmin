import asyncio

import httpx

from rosadmin.service import _lifespan, create_app


def test_healthz_returns_ok():
    app = create_app()

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/healthz")

    resp = asyncio.run(_call())
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_lifespan_runs_readiness_gate_without_systemd(monkeypatch):
    # No NOTIFY_SOCKET/WATCHDOG_USEC: the readiness gate runs, notify is a no-op,
    # and no watchdog task is spawned. Entering and exiting must not raise.
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    app = create_app()

    async def _run():
        async with _lifespan(app):
            pass

    asyncio.run(_run())
