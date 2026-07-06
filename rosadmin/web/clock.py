"""The one shared notion of "now" for the web layer's expiry and window checks.

Production code always calls `utcnow()` directly; session and rate-limit
stores accept an injected `Clock` so their expiry tests can control time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
