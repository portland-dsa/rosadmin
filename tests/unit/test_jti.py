from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rosadmin.web.jti import InMemoryJtiCache

_LATER = datetime.now(timezone.utc) + timedelta(seconds=30)


async def test_first_claim_wins_and_a_replay_loses():
    cache = InMemoryJtiCache()
    assert await cache.claim("jti-1", _LATER) is True
    assert await cache.claim("jti-1", _LATER) is False


async def test_distinct_jtis_each_win():
    cache = InMemoryJtiCache()
    assert await cache.claim("jti-1", _LATER) is True
    assert await cache.claim("jti-2", _LATER) is True
