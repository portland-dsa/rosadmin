"""Regression tests for the panel hardening pass.

Each pins a specific hole a security review found so it cannot silently reopen.
Both run without a database: the search test drives the assembled app over a test
client with in-memory stores, and the startup test raises before any connection is
opened.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rosadmin.service import create_app
from rosadmin.web.rate_limit import AUTH_RATE_LIMIT
from rosadmin.web.sessions import InMemorySessionStore
from rosadmin.web.settings import WebSettings


def test_search_route_is_rate_limited():
    # The member search must carry the limiter the mutation routes do, or one
    # session can bulk-enumerate the directory by email. No directory is wired, so
    # each search would answer 501 - but the limiter runs first, so the request past
    # the window is a 429. That 429 is what proves the dependency is in place.
    app = create_app(
        WebSettings(fake_login_enabled=False, allowed_origin="https://x"),
        session_store=InMemorySessionStore(),
    )
    with TestClient(app) as client:
        codes = [
            client.post(
                "/api/members/search", json={"email": "kris@example.com"}
            ).status_code
            for _ in range(AUTH_RATE_LIMIT + 1)
        ]
    assert all(code != 429 for code in codes[:AUTH_RATE_LIMIT])
    assert codes[-1] == 429


def test_production_build_refuses_to_start_without_an_origin():
    # No injected store means the real (production) wiring path, and no origin with
    # fake-login off is the misconfiguration that disarms the cross-origin guard.
    # Startup must refuse rather than serve. The raise precedes any DB connection,
    # so this needs no database.
    app = create_app(WebSettings(fake_login_enabled=False, allowed_origin=None))
    with pytest.raises(RuntimeError, match="ROSADMIN_ORIGIN"):
        with TestClient(app):
            pass
