"""A minimal sd_notify client - talk to systemd's notify socket, no dependency.

When the unit is `Type=notify`, systemd sets `NOTIFY_SOCKET` to a unix datagram
address (a leading `@` means an abstract socket, whose address starts with a NUL).
When the variable is unset - local dev, the test suite, the Windows workstation -
every function here is a no-op, so the service behaves the same with or without
systemd.
"""

from __future__ import annotations

import os
import socket


def _send(message: bytes) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:  # type: ignore[attr-defined]
        sock.connect(addr)
        sock.sendall(message)


def notify_ready() -> None:
    """Signal that the service has finished starting and is ready to serve."""
    _send(b"READY=1")


def notify_watchdog() -> None:
    """Pet the watchdog so systemd does not treat the service as hung."""
    _send(b"WATCHDOG=1")


def watchdog_interval() -> float | None:
    """Half the unit's `WatchdogSec` in seconds (the recommended ping cadence).

    Returns `None` when `WATCHDOG_USEC` is unset, so the caller skips the timer
    entirely outside systemd.
    """
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    return int(usec) / 1_000_000 / 2
