"""pytest's DB-layer fixtures, built on the shared `tests/support/pg.py` rig.

An autouse truncation keeps each test isolated while the session-scoped
container is reused for speed.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator

import pytest

from tests.support.pg import Db, Rig, start


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    # psycopg's async pool cannot run under Windows' default ProactorEventLoop;
    # it needs a selector loop. Linux (dev laptop and CI alike) keeps the
    # default policy untouched.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="session")
def rig() -> Iterator[Rig]:
    rig = start()
    try:
        yield rig
    finally:
        rig.stop()


@pytest.fixture(scope="session")
def database(rig: Rig) -> Db:
    return rig.db


@pytest.fixture(autouse=True)
def _clean(rig: Rig) -> None:
    rig.truncate()
