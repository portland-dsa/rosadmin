from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from fastapi.testclient import TestClient

from rosadmin.service import create_app
from rosadmin.sso import SigningKeys, SsoConfig, SsoSettings
from rosadmin.web.jti import InMemoryJtiCache
from rosadmin.web.rate_limit import (
    AUTH_RATE_LIMIT,
    InMemoryRateLimiter,
    _client_ip,
    rate_limited,
)
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings


async def test_counts_climb_within_a_window():
    limiter = InMemoryRateLimiter(
        clock=lambda: datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    )
    counts = [await limiter.hit("auth:/x:1.2.3.4") for _ in range(3)]
    assert counts == [1, 2, 3]


async def test_a_new_minute_resets_the_count():
    times = iter(
        [
            datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 5, 12, 1, tzinfo=timezone.utc),
        ]
    )
    limiter = InMemoryRateLimiter(clock=lambda: next(times))
    assert await limiter.hit("b") == 1
    assert await limiter.hit("b") == 1


def test_callback_429s_past_the_limit():
    # Susie floods /api/auth/callback from one address with no state cookie. That
    # missing-cookie guard returns its /?login=failed redirect before the handler
    # ever reaches botonio's socket, so the burst exercises the Depends(rate_limited)
    # wiring end to end on every platform without opening a unix socket. Each request
    # under the limit is that 302; the one past it is the limiter's 429.
    settings = SsoSettings(
        iss="botonio",
        aud="rosadmin",
        kid="v1",
        guild_id="42",
        socket_path="/tmp/none.sock",
    )
    app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None),
        session_store=InMemorySessionStore(),
        sso=SsoConfig(
            settings=settings, bearer="b", keys=SigningKeys({"v1": b"\x00" * 32})
        ),
        jti_cache=InMemoryJtiCache(),
        rate_limiter=InMemoryRateLimiter(),
    )
    with TestClient(app, follow_redirects=False) as client:
        responses = [
            client.get("/api/auth/callback") for _ in range(AUTH_RATE_LIMIT + 1)
        ]
    codes = [response.status_code for response in responses]
    assert all(code != 429 for code in codes[:AUTH_RATE_LIMIT])
    assert codes[-1] == 429
    assert responses[-1].json()["code"] == "rate_limited"


def _request_with_no_client() -> Request:
    # Spamton's garbled request: no Caddy header, and a bare ASGI scope with no
    # socket peer either.
    return Request({"type": "http", "headers": [], "client": None})


def test_client_ip_is_none_without_a_header_or_a_peer():
    assert _client_ip(_request_with_no_client()) is None


async def test_rate_limited_fails_open_without_a_header_or_a_peer():
    await rate_limited(_request_with_no_client())  # does not raise
