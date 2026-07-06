"""Single-use enforcement for assertion `jti`s: claim once, refuse a repeat."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class JtiCache(Protocol):
    """Burn a `jti` on first sight; a second claim of the same `jti` is a replay."""

    async def claim(self, jti: str, expires_at: datetime) -> bool:
        """`True` if `jti` was unseen and is now burned; `False` if already seen."""
        ...


class InMemoryJtiCache:
    """Scaffold cache: seen `jti`s live in a set and die with the process."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def claim(self, jti: str, expires_at: datetime) -> bool:
        if jti in self._seen:
            return False
        self._seen.add(jti)
        return True
