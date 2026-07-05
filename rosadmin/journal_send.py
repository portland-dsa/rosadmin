"""A minimal journald client for the audit mirror - native protocol, no dependency.

Mirrors `rosadmin.systemd_notify`: a datagram to systemd's journal socket. When
no socket is present (the Windows workstation, the test suite) emitting is a
benign no-op, so the audit row stays the record of truth and the mirror is
best-effort. When a socket is present but the send fails, that is a real loss and
raises `JournalSendError`, so a caller that fell back to the mirror can tell a
genuine failure from a dev-environment skip. Sending through the native protocol
rather than stdout is what lets each event carry its own `SYSLOG_IDENTIFIER`, so
audit and operational logs stay separable with `journalctl -t rosadmin-audit`.
"""

from __future__ import annotations

import os
import socket
import struct

_JOURNAL_SOCKET = "/run/systemd/journal/socket"
_IDENTIFIER = "rosadmin-audit"


class JournalSendError(Exception):
    """The journal socket was present but the datagram could not be delivered."""


def _field(name: str, value: str) -> bytes:
    raw = value.encode()
    if b"\n" in raw:
        return name.encode() + b"\n" + struct.pack("<Q", len(raw)) + raw + b"\n"
    return f"{name}={value}\n".encode()


def _send(fields: dict[str, str]) -> None:
    if not os.path.exists(_JOURNAL_SOCKET):
        return  # no journald here (dev workstation, tests): a benign no-op
    payload = b"".join(_field(key, value) for key, value in fields.items())
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:  # type: ignore[attr-defined]
            sock.connect(_JOURNAL_SOCKET)
            sock.sendall(payload)
    except OSError as error:
        raise JournalSendError(str(error)) from error


def audit(*, action: str, actor_hmac: str, subject_hmac: str | None) -> None:
    """Emit one audit event to journald on the `rosadmin-audit` identifier.

    A no-op where no journal socket exists; raises `JournalSendError` if a socket
    is present but the send fails.
    """
    fields = {
        "MESSAGE": f"audit: {action}",
        "PRIORITY": "6",
        "SYSLOG_IDENTIFIER": _IDENTIFIER,
        "AUDIT_ACTION": action,
        "AUDIT_ACTOR": actor_hmac,
    }
    if subject_hmac is not None:
        fields["AUDIT_SUBJECT"] = subject_hmac
    _send(fields)
