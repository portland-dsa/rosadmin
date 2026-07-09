"""The audit trail: append-only in Postgres, mirrored to journald for watching.

One choke point - `AuditSink.record` - so every audited event is uniform and the
observation mirror has a single place to hang. Actor and subject identifiers are
pseudonymized with keyed HMAC-SHA256; the raw ids never
land in the row or the log line, which is what makes the mirror safe to fan out.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from rosadmin import journal_send
from rosadmin.credentials import read_credential

_log = logging.getLogger(__name__)


def audit_key_from_env(env: Mapping[str, str]) -> bytes:
    """The audit HMAC key: a systemd credential on the box, env var in dev.

    Read through the shared [`read_credential`] from
    `$CREDENTIALS_DIRECTORY/audit-hmac-key` (how systemd delivers it) or
    `ROSADMIN_AUDIT_HMAC_KEY`. That the two delivery paths agree byte-for-byte
    matters here in particular: were a credential file's trailing newline to
    yield a different key than the same secret set inline, one actor's audit
    history would silently fork across two HMACs. The key is never logged.
    """
    raw = read_credential(env, "audit-hmac-key", "ROSADMIN_AUDIT_HMAC_KEY")
    if raw is None:
        raise RuntimeError("audit HMAC key is not configured")
    return raw.encode()


class AuditUnrecordedError(Exception):
    """Neither the durable row nor the journald mirror captured an event.

    Raised only when the database write failed and the fallback journald emit
    also failed, so the event was lost from both. Carries both underlying
    failures; the action verb and the pseudonymized ids it names are non-PII.
    """

    def __init__(
        self, action: str, db_error: Exception, journal_error: Exception
    ) -> None:
        super().__init__(
            f"audit event {action!r} was recorded nowhere: database write failed "
            f"({db_error}) and the journald mirror also failed ({journal_error})"
        )
        self.action = action
        self.db_error = db_error
        self.journal_error = journal_error


class AuditSink(Protocol):
    """Record one audited event. `actor`/`subject` are raw ids, pseudonymized here."""

    async def record(
        self,
        action: str,
        *,
        actor: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None: ...


async def record_best_effort(
    sink: AuditSink,
    action: str,
    *,
    actor: str,
    subject: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Audit an action that has already committed, without ever raising.

    The auth routes revoke or mint the session first, and that change is durable
    before the audit call. A `record` that fails afterwards - a present-but-broken
    journald, a database blip - must not turn the succeeded, already-persisted
    operation into a client 500, nor leave a freshly minted session with no cookie.
    The failure is logged for the operator (surfaced, not hidden) and stops here,
    rather than propagating out of a route whose real work is already done.
    """
    try:
        await sink.record(action, actor=actor, subject=subject, detail=detail)
    except (
        psycopg.Error,
        journal_send.JournalSendError,
        AuditUnrecordedError,
    ) as error:
        _log.error("audit of action %r failed after it committed: %s", action, error)


def _pseudonym(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


class PostgresAuditSink:
    """Write the audit row through the INSERT-only grant, then mirror to journald.

    The row is the record of truth and the journald line is its observable mirror.
    A journald that is merely absent (no socket, as in local dev) is tolerated
    silently, but a journald that is present and *fails* is a real problem and
    surfaces rather than hiding:

    - Row commits, mirror emits: normal.
    - Row commits, journald absent: normal - the mirror is skipped, no error.
    - Row commits, journald present but fails: the row stands and the journald
      error is raised, so a broken observability pipeline is noticed.
    - Row write fails, journald records it (or is benignly absent): the write
      error is raised; the event still reached journald where it could.
    - Row write fails and journald also fails: both records are lost, and an
      `AuditUnrecordedError` carries the pair.
    """

    def __init__(self, pool: AsyncConnectionPool, hmac_key: bytes) -> None:
        self._pool = pool
        self._key = hmac_key

    async def record(
        self,
        action: str,
        *,
        actor: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        actor_hmac = _pseudonym(self._key, actor)
        subject_hmac = _pseudonym(self._key, subject) if subject is not None else None

        db_error: psycopg.Error | None = None
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO audit_log (actor_hmac, subject_hmac, action, detail) "
                    "VALUES (%s, %s, %s, %s)",
                    (actor_hmac, subject_hmac, action, Jsonb(detail or {})),
                )
        except psycopg.Error as error:
            # The durable write did not happen (this also covers pool timeouts,
            # which subclass psycopg.Error).
            db_error = error

        journal_error: journal_send.JournalSendError | None = None
        try:
            journal_send.audit(
                action=action, actor_hmac=actor_hmac, subject_hmac=subject_hmac
            )
        except journal_send.JournalSendError as error:
            # A present-but-broken journald: the benign no-socket case never
            # raises, so reaching here is a genuine loss.
            journal_error = error

        if db_error is not None and journal_error is not None:
            raise AuditUnrecordedError(action, db_error, journal_error) from db_error
        if db_error is not None:
            raise db_error
        if journal_error is not None:
            raise journal_error


@dataclass(frozen=True)
class RecordedAudit:
    """One captured call to `RecordingAuditSink.record`."""

    action: str
    actor: str
    subject: str | None
    detail: dict[str, Any]


@dataclass
class RecordingAuditSink:
    """A hand-written fake: keeps calls in a list instead of touching Postgres.

    Used where a test wants to assert an event was recorded without a database,
    and as the injected default when the real sink is not wired.
    """

    records: list[RecordedAudit] = field(default_factory=list)

    async def record(
        self,
        action: str,
        *,
        actor: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.records.append(RecordedAudit(action, actor, subject, detail or {}))
