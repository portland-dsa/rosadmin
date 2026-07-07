"""`SolidarityTechClient.from_env`: `SOLIDARITY_TECH_MOCK` toggles which secret is
required.

A safety interlock rather than ordinary client behavior, and not exercised by
the persona-roster behave scenarios (they inject a client directly) - covered
here as the one targeted unit its branches need.
"""

from __future__ import annotations

import pytest

from rosadmin.membership.solidarity_tech.client import SolidarityTechClient


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
