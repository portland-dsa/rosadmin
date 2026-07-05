import asyncio
from typing import Any

import httpx
from fastapi.testclient import TestClient

from rosadmin import journal_send
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.service import _audit_key, _lifespan, create_app
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings


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


def test_audit_key_matches_across_file_and_env(tmp_path):
    # A credential file written with a trailing newline must yield the same key as
    # the same secret handed in via the env var - otherwise one actor's audit
    # history forks across two HMACs.
    (tmp_path / "audit-hmac-key").write_text("s3cret-key\n", encoding="utf-8")

    from_file = _audit_key({"CREDENTIALS_DIRECTORY": str(tmp_path)})
    from_env = _audit_key({"ROSADMIN_AUDIT_HMAC_KEY": "s3cret-key"})

    assert from_file == from_env == b"s3cret-key"


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
