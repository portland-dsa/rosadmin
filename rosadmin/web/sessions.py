"""Server-side sessions: opaque token in, leader context out."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID

ABSOLUTE_LIFETIME = timedelta(hours=12)
IDLE_TIMEOUT = timedelta(hours=2)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LeaderContext:
    """Everything a route handler may know about the logged-in leader."""

    member_id: UUID
    display_name: str
    managed_group_ids: frozenset[UUID]


class SessionStore(Protocol):
    """Create, resolve, and revoke sessions; resolution refreshes the idle clock."""

    async def create(self, leader: LeaderContext) -> str: ...

    async def resolve(self, token: str) -> LeaderContext | None: ...

    async def revoke(self, token: str) -> None: ...


@dataclass
class _Entry:
    leader: LeaderContext
    created: datetime
    last_seen: datetime


class InMemorySessionStore:
    """Scaffold store: sessions live in a dict and die with the process.

    The injectable clock exists for expiry tests; production code never
    passes one.
    """

    def __init__(self, clock: Clock = _utcnow) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    async def create(self, leader: LeaderContext) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._entries[token] = _Entry(leader=leader, created=now, last_seen=now)
        return token

    async def resolve(self, token: str) -> LeaderContext | None:
        entry = self._entries.get(token)
        if entry is None:
            return None
        now = self._clock()
        expired = (
            now - entry.created >= ABSOLUTE_LIFETIME
            or now - entry.last_seen >= IDLE_TIMEOUT
        )
        if expired:
            del self._entries[token]
            return None
        entry.last_seen = now
        return entry.leader

    async def revoke(self, token: str) -> None:
        self._entries.pop(token, None)
