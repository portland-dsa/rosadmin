"""The live Solidarity Tech adapter: an httpx client behind the `MembershipSource` port.

Reads `GET /users` with offset paging and a bearer token. `list_members` is the
lenient roster sweep (any decode failure is skipped); `find_by_email` is the
targeted lookup (a record with no email is skipped, every other decode failure is
surfaced).
"""

from __future__ import annotations

import os

import httpx

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.source import Member

#: The public API base URL, used when no `SOLIDARITY_TECH_BASE_URL` override is set.
API_BASE_URL = "https://api.solidarity.tech/v1"

#: `_limit` page size. A unique email returns 0-1 rows, so one page covers a lookup;
#: the cap only bounds the roster sweep's page size.
PAGE_SIZE = 100


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
    ) -> None:
        self._token = token
        resolved = (
            base_url or os.environ.get("SOLIDARITY_TECH_BASE_URL") or API_BASE_URL
        )
        self._base_url = resolved.rstrip("/")
        self._client = client or httpx.AsyncClient()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def list_members(self) -> list[Member]:
        """Page the whole collection, skipping any record that fails to decode."""
        members: list[Member] = []
        offset = 0
        while True:
            resp = await self._client.get(
                f"{self._base_url}/users",
                params={"_limit": PAGE_SIZE, "_offset": offset},
                headers=self._headers(),
            )
            resp.raise_for_status()
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
        resp = await self._client.get(
            f"{self._base_url}/users",
            params={"email": email, "_limit": PAGE_SIZE},
            headers=self._headers(),
        )
        resp.raise_for_status()
        for raw in resp.json().get("data", []):
            try:
                return decode_user(raw)
            except MalformedMember:
                continue  # no email to project; a DecodeError, by contrast, propagates
        return None

    async def aclose(self) -> None:
        """Close the underlying httpx client, releasing its connection pool."""
        await self._client.aclose()
