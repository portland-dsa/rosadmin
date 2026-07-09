import asyncio
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import AsyncConnectionPool

from rosadmin import journal_send, service
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.service import _lifespan, _start_admin, create_app
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings

#: `_start_admin`'s gate returns before ever touching the pool, so a stand-in
#: object - cast to the type it never dereferences - is enough for the gating
#: tests without opening a real database connection.
_NO_POOL = cast(AsyncConnectionPool, object())


def test_healthz_returns_ok():
    app = create_app(
        session_store=InMemorySessionStore(), audit_sink=RecordingAuditSink()
    )

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/api/healthz")

    resp = asyncio.run(_call())
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_lifespan_runs_readiness_gate_without_systemd(monkeypatch):
    # No NOTIFY_SOCKET/WATCHDOG_USEC: the readiness gate runs, notify is a no-op,
    # and no watchdog task is spawned. Entering and exiting must not raise.
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    app = create_app(
        session_store=InMemorySessionStore(), audit_sink=RecordingAuditSink()
    )

    async def _run():
        async with _lifespan(app):
            pass

    asyncio.run(_run())


class _BrokenAuditSink:
    """An audit sink whose journald mirror is present but fails on every call."""

    async def record(
        self,
        action: str,
        *,
        actor: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        raise journal_send.JournalSendError("journald socket present but broken")


def test_audit_failure_does_not_poison_committed_auth():
    # The session mint and revoke are durable before the audit call, so a broken
    # audit mirror must not turn a succeeded login/logout into a 500 or leave a
    # minted session with no cookie set.
    app = create_app(
        WebSettings(fake_login_enabled=True, allowed_origin=None),
        session_store=InMemorySessionStore(),
        audit_sink=_BrokenAuditSink(),
    )
    client = TestClient(app, base_url="https://testserver")

    login = client.post("/api/auth/fake-login", json={"persona": "leader"})
    assert login.status_code == 200, login.text
    assert client.cookies.get("rosadmin_session") is not None

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204, logout.text


async def test_admin_socket_unset_starts_no_admin_task():
    # rosadmin-admintools is installed in this dev environment, so the only gate
    # that can fail here is the env var - confirming the "package absent" half
    # needs the separate monkeypatched case below.
    assert await _start_admin(_NO_POOL, {}) is None


async def test_admin_package_absent_starts_no_admin_task(monkeypatch):
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda name: None)
    env = {"ROSADMIN_ADMIN_SOCKET": "/tmp/rosadmin-admin.sock"}
    assert await _start_admin(_NO_POOL, env) is None


async def test_mock_st_unset_starts_no_mock_server():
    assert await service._start_mock_st({}) is None


async def test_mock_st_without_a_base_url_refuses_to_boot():
    with pytest.raises(RuntimeError, match="SOLIDARITY_TECH_BASE_URL"):
        await service._start_mock_st({"SOLIDARITY_TECH_MOCK": "1"})


async def test_mock_st_without_an_explicit_port_refuses_to_boot():
    # The mock must bind exactly where the client will call; a portless base
    # would leave the OS to pick one the client never reads.
    env = {
        "SOLIDARITY_TECH_MOCK": "1",
        "SOLIDARITY_TECH_BASE_URL": "http://127.0.0.1",
    }
    with pytest.raises(RuntimeError, match="host and port"):
        await service._start_mock_st(env)


def test_resource_routes_501_without_a_directory():
    # A build with no directory wired (a deployed build, today) answers a stable
    # 501 rather than a 500 or a misleading empty result.
    app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None),
        session_store=InMemorySessionStore(),
    )
    with TestClient(app) as client:
        response = client.get("/api/me")
    assert response.status_code == 501
    assert response.json()["code"] == "reads_not_available"
