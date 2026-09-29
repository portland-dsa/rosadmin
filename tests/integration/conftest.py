"""pytest's DB-layer fixtures, built on the shared `tests/support/pg.py` rig.

An autouse truncation keeps each database test isolated while the
session-scoped container is reused for speed. A test that never asks for
`database` - the live Google test shares this folder - never starts the
container either, so it runs on a machine with no working container runtime.
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
def _clean(request: pytest.FixtureRequest) -> None:
    if "database" in request.fixturenames:
        request.getfixturevalue("rig").truncate()
