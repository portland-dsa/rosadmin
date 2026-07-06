"""Server-side sessions: opaque token in, authenticated principal out."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from rosadmin.sso import DiscordUserId
from rosadmin.web.clock import Clock, utcnow

ABSOLUTE_LIFETIME = timedelta(hours=12)
IDLE_TIMEOUT = timedelta(hours=2)


@dataclass(frozen=True)
class Principal:
    """The authenticated subject of a session: a verified Discord identity.

    All a real login knows is the Discord snowflake. The enriched view - the
    member record, display name, and the groups they may manage - is derived
    from this principal against the records, and lands with records-based
    authorization; it is deliberately not stored on the session here.
    """

    discord_id: DiscordUserId


class SessionStore(Protocol):
    """Create, resolve, and revoke sessions; resolution refreshes the idle clock."""

    async def create(self, principal: Principal) -> str: ...

    async def resolve(self, token: str) -> Principal | None: ...

    async def revoke(self, token: str) -> None: ...


@dataclass
class _Entry:
    principal: Principal
    created: datetime
    last_seen: datetime


class InMemorySessionStore:
    """Scaffold store: sessions live in a dict and die with the process.

    The injectable clock exists for expiry tests; production code never
    passes one.
    """

    def __init__(self, clock: Clock = utcnow) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    async def create(self, principal: Principal) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._entries[token] = _Entry(principal=principal, created=now, last_seen=now)
        return token

    async def resolve(self, token: str) -> Principal | None:
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
        return entry.principal

    async def revoke(self, token: str) -> None:
        self._entries.pop(token, None)
