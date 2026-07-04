from __future__ import annotations

import os

import pytest

from rosadmin.cli import _listen_target, serve


def test_no_systemd_environment_means_no_fd():
    assert _listen_target({}) is None


def test_matching_pid_with_one_fd_yields_fd_three():
    env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}
    assert _listen_target(env) == 3


def test_foreign_pid_is_ignored():
    env = {"LISTEN_PID": "1", "LISTEN_FDS": "1"}
    assert _listen_target(env) is None


def test_garbage_fd_counts_are_ignored():
    env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "zero"}
    assert _listen_target(env) is None


def test_refuses_tcp_fallback_under_systemd(monkeypatch: pytest.MonkeyPatch):
    # A notify environment with no delivered fd means the socket unit failed
    # us; binding TCP would silently bypass the ingress group. uvicorn.run is
    # stubbed to fail loudly so a guard regression cannot bind a real server
    # and hang the runner.
    import uvicorn

    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.delenv("LISTEN_PID", raising=False)
    monkeypatch.delenv("LISTEN_FDS", raising=False)
    monkeypatch.setattr(
        uvicorn, "run", lambda *a, **kw: pytest.fail("guard did not refuse")
    )
    with pytest.raises(SystemExit, match="refusing the TCP fallback"):
        serve()


def test_explicit_uds_under_systemd_is_not_refused(monkeypatch: pytest.MonkeyPatch):
    # An operator who wires ExecStart with --uds chose their listener; only
    # the silent TCP downgrade is refused. uvicorn.run is stubbed out so the
    # test observes the dispatch without binding anything.
    import uvicorn

    seen: dict[str, object] = {}
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.delenv("LISTEN_PID", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    serve(uds="/tmp/dev.sock")
    assert seen.get("uds") == "/tmp/dev.sock"
