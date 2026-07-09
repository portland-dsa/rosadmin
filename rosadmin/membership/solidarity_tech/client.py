"""The live Solidarity Tech adapter: an httpx client behind the `MembershipSource` port.

Reads `GET /users` with offset paging and a bearer token. `list_members` is the
lenient roster sweep (any decode failure is skipped); `find_by_email` is the
targeted lookup (a record with no email is skipped, every other decode failure is
surfaced).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

import httpx
from aiolimiter import AsyncLimiter

from rosadmin.credentials import read_credential
from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.source import Member

logger = logging.getLogger(__name__)

#: The public API base URL, used when no `SOLIDARITY_TECH_BASE_URL` override is set.
API_BASE_URL = "https://api.solidarity.tech/v1"

#: `_limit` page size. A unique email returns 0-1 rows, so one page covers a lookup;
#: the cap only bounds the roster sweep's page size.
PAGE_SIZE = 100

#: The pacer's default budget: Solidarity Tech allows 60 requests per 30
#: seconds, and 55 leaves a margin so a window-boundary race cannot trip the
#: limit. The leaky bucket lets a pull burst through its first pages at full
#: speed and only then settles to the steady rate. This is this client's
#: default *share* of that budget - a deployment where another consumer also
#: holds the key sets `SOLIDARITY_TECH_REQUEST_BUDGET` so the shares sum
#: under the limit.
_REQUESTS_PER_WINDOW = 55

#: How many times a 429 is waited out before it is surfaced to the caller.
_RATE_LIMIT_ATTEMPTS = 5

#: Seconds in the rate-limit window - the bucket's refill period, and the wait
#: after a 429 that carries no usable Retry-After header, so the next attempt
#: starts from a clean slate.
_RATE_LIMIT_WINDOW = 30.0


class SolidarityTechClient:
    """An httpx-backed Solidarity Tech client. Satisfies `MembershipSource`.

    `base_url` falls back to the `SOLIDARITY_TECH_BASE_URL` environment variable
    and then to `API_BASE_URL`, so a staging instance can point at a mock without
    a code change. Pass `client` to inject an `httpx.AsyncClient` (the tests wire
    one onto the in-process mock).
    """

    def __init__(
        self,
        token: str,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        requests_per_window: int | None = None,
    ) -> None:
        self._token = token
        resolved = (
            base_url or os.environ.get("SOLIDARITY_TECH_BASE_URL") or API_BASE_URL
        )
        self._base_url = resolved.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._limiter = AsyncLimiter(
            requests_per_window
            if requests_per_window is not None
            else _REQUESTS_PER_WINDOW,
            _RATE_LIMIT_WINDOW,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SolidarityTechClient:
        """Build a client from the environment, toggled by `SOLIDARITY_TECH_MOCK`.

        This is a PII blast-radius interlock, not a write guard - the client is
        read-only either way, so the only thing the toggle protects is which
        roster (real members or fabricated personas) a pull can read.

        Unset: real Solidarity Tech. The token is required (`read_credential` on
        `solidarity_tech_token`/`SOLIDARITY_TECH_TOKEN`) - a targeted command must
        not run tokenless - and the base URL takes the constructor's usual
        real-API fallback.

        Truthy: the mock. `SOLIDARITY_TECH_BASE_URL` is required - refusing to
        guess a mock target rather than silently falling through to the real API
        - and the token is optional, since the mock ignores auth.

        `SOLIDARITY_TECH_REQUEST_BUDGET`, when set, overrides this client's
        share of the key's request budget in either branch - a deployment
        sharing the token with another consumer lowers it so the shares sum
        under Solidarity Tech's own limit.
        """
        budget_raw = env.get("SOLIDARITY_TECH_REQUEST_BUDGET")
        budget: int | None = None
        if budget_raw is not None:
            try:
                budget = int(budget_raw)
            except ValueError as error:
                raise RuntimeError(
                    "SOLIDARITY_TECH_REQUEST_BUDGET must be a positive integer"
                ) from error
            if budget < 1:
                raise RuntimeError(
                    "SOLIDARITY_TECH_REQUEST_BUDGET must be a positive integer"
                )

        if env.get("SOLIDARITY_TECH_MOCK"):
            base_url = env.get("SOLIDARITY_TECH_BASE_URL")
            if not base_url:
                raise RuntimeError(
                    "SOLIDARITY_TECH_MOCK is set but SOLIDARITY_TECH_BASE_URL is "
                    "not; refusing to guess a mock target"
                )
            token = (
                read_credential(env, "solidarity_tech_token", "SOLIDARITY_TECH_TOKEN")
                or ""
            )
            return cls(token=token, base_url=base_url, requests_per_window=budget)

        token = read_credential(env, "solidarity_tech_token", "SOLIDARITY_TECH_TOKEN")
        if token is None:
            raise RuntimeError(
                "SOLIDARITY_TECH_TOKEN (or the solidarity_tech_token credential) is "
                "not configured; refusing to run against the real API without one"
            )
        # Resolve the base URL from the passed mapping, not the process environment,
        # so `from_env` honors its `env` argument consistently in both branches.
        return cls(
            token=token,
            base_url=env.get("SOLIDARITY_TECH_BASE_URL"),
            requests_per_window=budget,
        )

    def _headers(self) -> dict[str, str]:
        # The mock ignores auth and `from_env`'s mock branch allows an empty
        # token, which a bearer value of "Bearer " (httpx/h11 rejects a header
        # value with trailing whitespace) cannot express - omit the header
        # instead of sending a malformed one.
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _get_users(self, params: dict[str, str | int]) -> httpx.Response:
        """One `GET /users`, paced under the rate budget, with a 429 safety net.

        The limiter keeps requests inside Solidarity Tech's budget on its own;
        the retry loop is the net for when that assumption and the server's
        accounting disagree (another consumer sharing the token, a clock-edge
        miscount). A 429 waits out `Retry-After` when the server names a
        delay, else a full window, and is surfaced only once the attempts are
        spent. Every other error status raises immediately.
        """
        attempts = _RATE_LIMIT_ATTEMPTS
        while True:
            async with self._limiter:
                resp = await self._client.get(
                    f"{self._base_url}/users", params=params, headers=self._headers()
                )
            attempts -= 1
            if resp.status_code != 429 or attempts == 0:
                resp.raise_for_status()
                return resp
            # Parse-or-fallback rather than an isdigit guard: isdigit accepts
            # Unicode digits float() rejects (the same mismatch decode.py
            # documents), and a header may also carry an HTTP-date this does
            # not attempt to honor. Anything unparseable or outside a sane
            # non-negative bound falls back to the full window.
            try:
                wait = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                wait = _RATE_LIMIT_WINDOW
            if not (0 <= wait <= 10 * _RATE_LIMIT_WINDOW):
                wait = _RATE_LIMIT_WINDOW
            logger.warning("rate limited by Solidarity Tech; waiting %.0fs", wait)
            await asyncio.sleep(wait)

    async def list_members(self) -> list[Member]:
        """Page the whole collection, skipping any record that fails to decode."""
        members: list[Member] = []
        offset = 0
        while True:
            resp = await self._get_users({"_limit": PAGE_SIZE, "_offset": offset})
            body = resp.json()
            data = body.get("data", [])
            for raw in data:
                try:
                    members.append(decode_user(raw))
                except (MalformedMember, DecodeError):
                    continue  # lenient sweep: one bad record never aborts the run
            total = (body.get("meta") or {}).get("total_count")
            offset += PAGE_SIZE
            if len(data) < PAGE_SIZE or (total is not None and offset >= total):
                break
        return members

    async def find_by_email(self, email: str) -> Member | None:
        """Return the first match for `email`, or `None`; surface a `DecodeError`."""
        resp = await self._get_users({"email": email, "_limit": PAGE_SIZE})
        for raw in resp.json().get("data", []):
            try:
                return decode_user(raw)
            except MalformedMember:
                continue  # no email to project; a DecodeError, by contrast, propagates
        return None

    async def aclose(self) -> None:
        """Close the underlying httpx client, releasing its connection pool."""
        await self._client.aclose()
