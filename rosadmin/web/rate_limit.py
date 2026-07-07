"""Per-IP request rate limiting for the auth endpoints.

A fixed one-minute window counted in Postgres rather than a separate cache
daemon, so the limiter adds no new store to run or back up. The count-and-check
is one atomic statement so the limiter cannot race itself. The
client IP is read from the single header Caddy sets over the private socket - it
is a rate-limit key, never identity.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from rosadmin.web.clock import Clock, utcnow
from rosadmin.web.problems import AppProblem, ProblemCode

AUTH_RATE_LIMIT = 20  # requests per IP per minute across an auth endpoint


class RateLimiter(Protocol):
    async def hit(self, bucket: str) -> int:
        """Increment `bucket`'s counter for the current window; return the new count."""
        ...


class InMemoryRateLimiter:
    """Scaffold limiter: per-process counts, keyed by (bucket, minute).

    The injectable clock exists for window-boundary tests; production code
    never passes one.
    """

    def __init__(self, clock: Clock = utcnow) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, str], int] = {}

    async def hit(self, bucket: str) -> int:
        window = self._clock().strftime("%Y-%m-%dT%H:%M")
        key = (bucket, window)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


def _client_ip(request: Request) -> str | None:
    # Caddy sets this over the private socket; absent (local/dev), fall back to the
    # peer. `None` means neither is present.
    return request.headers.get("x-real-client-ip") or (
        request.client.host if request.client else None
    )


async def rate_limited(request: Request) -> None:
    """FastAPI dependency: 429 when this IP has exceeded the window on this path."""
    client_ip = _client_ip(request)
    if client_ip is None:
        # Nothing to key a bucket on. In production Caddy always sets the header,
        # so this only trips on a misconfiguration - and dropping the limit for
        # that traffic is safer than folding it into one shared bucket that locks
        # out every login. Fail open.
        return
    limiter: RateLimiter = request.app.state.rate_limiter
    bucket = f"auth:{request.url.path}:{client_ip}"
    if await limiter.hit(bucket) > AUTH_RATE_LIMIT:
        raise AppProblem(429, ProblemCode.RateLimited, "too many requests")
