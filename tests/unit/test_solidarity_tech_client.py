"""Targeted units for `SolidarityTechClient` branches the behave scenarios
cannot reach.

`from_env` is a safety interlock rather than ordinary client behavior, and the
persona-roster scenarios inject a client directly, bypassing it. The 429
handling pins a live-found failure: an unpaced pull tripped Solidarity Tech's
rate limit mid-sweep and crashed instead of waiting it out.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from rosadmin.membership.solidarity_tech.client import (
    _RATE_LIMIT_ATTEMPTS,
    SolidarityTechClient,
)


def _client_returning(responses: list[httpx.Response]) -> SolidarityTechClient:
    """A client whose transport replays `responses` in order, no network."""
    replay = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replay)

    return SolidarityTechClient(
        token="t0ken",
        base_url="http://replay.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _rate_limited() -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": "0"})


_EMPTY_PAGE = {"data": [], "meta": {"total_count": 0}}


def test_a_429_is_waited_out_and_the_pull_continues():
    client = _client_returning([_rate_limited(), httpx.Response(200, json=_EMPTY_PAGE)])
    assert asyncio.run(client.list_members()) == []


def test_a_persistent_429_is_surfaced_once_the_attempts_are_spent():
    client = _client_returning([_rate_limited() for _ in range(_RATE_LIMIT_ATTEMPTS)])
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        asyncio.run(client.list_members())
    assert excinfo.value.response.status_code == 429


@pytest.mark.parametrize(
    "env",
    [
        {},  # real mode: no token
        {"SOLIDARITY_TECH_MOCK": "1"},  # mock mode: no base URL
    ],
)
def test_from_env_refuses_when_its_required_secret_is_absent(env):
    with pytest.raises(RuntimeError):
        SolidarityTechClient.from_env(env)


@pytest.mark.parametrize(
    "env",
    [
        {"SOLIDARITY_TECH_TOKEN": "t0ken"},  # real mode: token present
        {
            "SOLIDARITY_TECH_MOCK": "1",
            "SOLIDARITY_TECH_BASE_URL": "http://mock.test",
        },  # mock mode: base URL present, token optional
    ],
)
def test_from_env_builds_a_client_when_its_required_secret_is_present(env):
    assert isinstance(SolidarityTechClient.from_env(env), SolidarityTechClient)
