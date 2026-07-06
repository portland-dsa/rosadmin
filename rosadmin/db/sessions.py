"""Server-side sessions in Postgres: the durable home of the `SessionStore` port.

The opaque token lives only in the client cookie; the table holds its sha256, so
a database read cannot lift a live session. `resolve` refreshes the idle clock
and enforces both expiries in one statement, reusing `ABSOLUTE_LIFETIME` and
`IDLE_TIMEOUT` so the expiry rule is defined once and shared with the in-memory
fake.
"""

from __future__ import annotations

import hashlib
import secrets

from psycopg_pool import AsyncConnectionPool

from rosadmin.sso import DiscordUserId
from rosadmin.web.sessions import ABSOLUTE_LIFETIME, IDLE_TIMEOUT, Principal


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class PostgresSessionStore:
    """The `SessionStore` backed by the `sessions` table."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, principal: Principal) -> str:
        token = secrets.token_urlsafe(32)
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO sessions (token_hash, discord_id) VALUES (%s, %s)",
                (_hash(token), principal.discord_id),
            )
        return token

    async def resolve(self, token: str) -> Principal | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE sessions SET last_seen_at = now() "
                "WHERE token_hash = %s "
                "  AND created_at > now() - %s "
                "  AND last_seen_at > now() - %s "
                "RETURNING discord_id",
                (_hash(token), ABSOLUTE_LIFETIME, IDLE_TIMEOUT),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return Principal(discord_id=DiscordUserId(row[0]))

    async def revoke(self, token: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM sessions WHERE token_hash = %s", (_hash(token),)
            )
