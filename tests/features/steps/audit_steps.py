# behave's @given/@when/@then resolve to a decorator Pylance treats as non-callable.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio

import psycopg
from behave import given, then, when

from rosadmin import journal_send
from rosadmin.db.audit import AuditUnrecordedError, PostgresAuditSink


class _RecordingPool:
    """A stand-in pool that captures the audit INSERT instead of touching Postgres."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, object]] = []

    def connection(self) -> "_RecordingConn":
        return _RecordingConn(self)


class _RecordingConn:
    def __init__(self, pool: _RecordingPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> "_RecordingConn":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def execute(self, query: str, params: object = None) -> None:
        self._pool.inserts.append((query, params))


class _FailingPool:
    """A stand-in pool whose connection checkout always raises a database error."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, object]] = []

    def connection(self) -> "_FailingConn":
        return _FailingConn()


class _FailingConn:
    async def __aenter__(self) -> None:
        raise psycopg.OperationalError("database is down")

    async def __aexit__(self, *_args: object) -> bool:
        return False


@given("the audit database is {state}")
def step_database(context, state):
    context.audit_pool = _RecordingPool() if state == "up" else _FailingPool()


@given("the journald socket is {state}")
def step_journald(context, state):
    context.journald = []
    original = journal_send.audit

    def present(**kwargs):
        context.journald.append(kwargs["action"])

    def understandably_missing(**_kwargs):
        return  # no socket here: a benign no-op

    def incorrectly_missing(**_kwargs):
        raise journal_send.JournalSendError("socket present but unreachable")

    fake = {
        "present": present,
        "understandably missing": understandably_missing,
        "incorrectly missing": incorrectly_missing,
    }[state]
    setattr(journal_send, "audit", fake)
    context.add_cleanup(lambda: setattr(journal_send, "audit", original))


@when("Ralsei's action is recorded to the audit log")
def step_record(context):
    sink = PostgresAuditSink(context.audit_pool, b"test-key")  # type: ignore[arg-type]
    context.error = None
    try:
        asyncio.run(sink.record("login", actor="ralsei"))
    except Exception as error:  # classified in the outcome step below
        context.error = error


@then("the audit database write {result}")
def step_db_write(context, result):
    wrote = len(context.audit_pool.inserts) > 0
    assert wrote == (result == "happens"), context.audit_pool.inserts


@then("the journald mirror {result}")
def step_journald_write(context, result):
    wrote = len(context.journald) > 0
    assert wrote == (result == "happens"), context.journald


@then("the recording call {outcome}")
def step_outcome(context, outcome):
    expected = {
        "succeeds": None,
        "raises a database error": psycopg.Error,
        "raises a journald error": journal_send.JournalSendError,
        "raises a compound error": AuditUnrecordedError,
    }[outcome]
    if expected is None:
        assert context.error is None, context.error
    else:
        assert isinstance(context.error, expected), context.error
