"""A second sweep is refused while the first holds the advisory lock."""

from __future__ import annotations

import psycopg
import pytest

from rosadmin.db import make_pool
from rosadmin.db.audit import RecordingAuditSink
from rosadmin.group_sync import RecordingGroupSync
from rosadmin.membership.source import Email
from rosadmin.reconcile import _SWEEP_LOCK_KEY, SweepAlreadyRunning, run_sweep

pytestmark = pytest.mark.integration


async def test_sweep_refused_while_lock_held(database) -> None:
    pool = make_pool(database.app_dsn)
    await pool.open()
    holder = await psycopg.AsyncConnection.connect(database.app_dsn, autocommit=True)
    try:
        got = await (
            await holder.execute("SELECT pg_try_advisory_lock(%s)", (_SWEEP_LOCK_KEY,))
        ).fetchone()
        assert got is not None and got[0]
        with pytest.raises(SweepAlreadyRunning):
            await run_sweep(
                pool,
                source=None,
                lister=None,
                sync=RecordingGroupSync(),
                audit=RecordingAuditSink(),
                main_group_email=Email("everyone@example.net"),
            )
        await holder.execute("SELECT pg_advisory_unlock(%s)", (_SWEEP_LOCK_KEY,))
        # released: the same call proceeds (lister=None -> desired-only report)
        report = await run_sweep(
            pool,
            source=None,
            lister=None,
            sync=RecordingGroupSync(),
            audit=RecordingAuditSink(),
            main_group_email=Email("everyone@example.net"),
        )
        assert report.lister_available is False
        assert report.groups == ()
    finally:
        await holder.close()
        await pool.close()
