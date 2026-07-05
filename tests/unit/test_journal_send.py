from __future__ import annotations

import sys

import pytest

from rosadmin import journal_send


def test_simple_field_is_key_equals_value_newline():
    assert journal_send._field("AUDIT_ACTION", "login") == b"AUDIT_ACTION=login\n"


def test_multiline_field_uses_length_framing():
    encoded = journal_send._field("MESSAGE", "a\nb")
    assert encoded.startswith(b"MESSAGE\n")
    assert encoded.endswith(b"a\nb\n")


def test_audit_is_a_noop_without_a_journal_socket(monkeypatch):
    # The dev/test machine has no journal socket; emitting must not raise.
    monkeypatch.setattr(journal_send.os.path, "exists", lambda _p: False)
    journal_send.audit(action="login", actor_hmac="deadbeef", subject_hmac=None)


@pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX datagram is POSIX-only")
def test_audit_raises_when_send_fails_with_a_socket_present(monkeypatch):
    # A socket exists but the datagram cannot be delivered: a genuine loss, not a
    # benign dev-environment skip, so it must surface.
    monkeypatch.setattr(journal_send.os.path, "exists", lambda _p: True)

    def _boom(*_args, **_kwargs):
        raise OSError("no route to journald")

    monkeypatch.setattr(journal_send.socket, "socket", _boom)
    with pytest.raises(journal_send.JournalSendError):
        journal_send.audit(action="login", actor_hmac="deadbeef", subject_hmac=None)
