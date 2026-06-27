import socket
import sys

import pytest

from rosadmin import systemd_notify


def test_send_is_a_noop_without_notify_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # Must not raise and must not touch a socket.
    systemd_notify.notify_ready()
    systemd_notify.notify_watchdog()


def test_watchdog_interval_is_half_of_usec(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")  # 30s
    assert systemd_notify.watchdog_interval() == 15.0


def test_watchdog_interval_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert systemd_notify.watchdog_interval() is None


@pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX datagram is POSIX-only")
def test_send_writes_ready_to_the_notify_socket(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)  # type: ignore[attr-defined]
    server.bind(sock_path)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
        systemd_notify.notify_ready()
        server.settimeout(2)
        assert server.recv(64) == b"READY=1"
    finally:
        server.close()
