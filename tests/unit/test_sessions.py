from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from rosadmin.web.sessions import (
    ABSOLUTE_LIFETIME,
    IDLE_TIMEOUT,
    InMemorySessionStore,
    LeaderContext,
)

_LEADER = LeaderContext(
    member_id=uuid4(), display_name="Ralsei", managed_group_ids=frozenset()
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


@pytest.mark.asyncio
async def test_create_then_resolve_round_trips():
    store = InMemorySessionStore()
    token = await store.create(_LEADER)
    assert await store.resolve(token) == _LEADER


@pytest.mark.asyncio
async def test_unknown_and_revoked_tokens_resolve_to_none():
    store = InMemorySessionStore()
    assert await store.resolve("nope") is None
    token = await store.create(_LEADER)
    await store.revoke(token)
    assert await store.resolve(token) is None


@pytest.mark.asyncio
async def test_idle_expiry_but_activity_extends():
    clock = _FakeClock()
    store = InMemorySessionStore(clock=clock)
    token = await store.create(_LEADER)
    clock.advance(IDLE_TIMEOUT - timedelta(minutes=1))
    assert await store.resolve(token) is not None  # refreshes last_seen
    clock.advance(IDLE_TIMEOUT - timedelta(minutes=1))
    assert await store.resolve(token) is not None
    clock.advance(IDLE_TIMEOUT)
    assert await store.resolve(token) is None


@pytest.mark.asyncio
async def test_absolute_expiry_ignores_activity():
    clock = _FakeClock()
    store = InMemorySessionStore(clock=clock)
    token = await store.create(_LEADER)
    step = IDLE_TIMEOUT - timedelta(minutes=1)
    while clock.now - datetime(2026, 1, 1, tzinfo=timezone.utc) < ABSOLUTE_LIFETIME:
        clock.advance(step)
        await store.resolve(token)
    assert await store.resolve(token) is None
