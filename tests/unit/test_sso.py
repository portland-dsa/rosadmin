from __future__ import annotations

import pytest

from rosadmin.sso import (
    SigningKeys,
    SsoConfigError,
    SsoSettings,
    UnknownKeyId,
    sso_settings_from_env,
)


def test_settings_default_the_fixed_contract_values():
    # Only the two per-environment values are supplied; iss/aud/kid fall to the
    # frozen contract defaults.
    settings = sso_settings_from_env(
        {
            "BOTONIO_SSO_GUILD_ID": "42",
            "BOTONIO_SSO_SOCKET_PATH": "/tmp/botonio-sso.sock",
        }
    )
    assert settings == SsoSettings(
        iss="botonio",
        aud="rosadmin",
        kid="v1",
        guild_id="42",
        socket_path="/tmp/botonio-sso.sock",
    )


def test_settings_take_overrides():
    settings = sso_settings_from_env(
        {
            "BOTONIO_SSO_ISS": "other-iss",
            "BOTONIO_SSO_AUD": "other-aud",
            "BOTONIO_SSO_KID": "v2",
            "BOTONIO_SSO_GUILD_ID": "99",
            "BOTONIO_SSO_SOCKET_PATH": "/run/botonio-staging/sso.sock",
        }
    )
    assert (settings.iss, settings.aud, settings.kid) == (
        "other-iss",
        "other-aud",
        "v2",
    )
    assert settings.socket_path == "/run/botonio-staging/sso.sock"


@pytest.mark.parametrize("missing", ["BOTONIO_SSO_GUILD_ID", "BOTONIO_SSO_SOCKET_PATH"])
def test_settings_require_the_per_environment_values(missing):
    # The guild id and socket path have no safe default, so their absence is a
    # loud configuration error rather than a silent wrong target.
    env = {
        "BOTONIO_SSO_GUILD_ID": "42",
        "BOTONIO_SSO_SOCKET_PATH": "/tmp/botonio-sso.sock",
    }
    del env[missing]
    with pytest.raises(SsoConfigError):
        sso_settings_from_env(env)


def test_signing_keys_return_the_pinned_key():
    keys = SigningKeys({"v1": b"\x01" * 32})
    assert keys.key_for("v1") == b"\x01" * 32


def test_signing_keys_refuse_an_unknown_kid():
    # A kid outside the pinned map is refused, never trusted - the token does not
    # get to choose a verifier rosadmin never held.
    keys = SigningKeys({"v1": b"\x01" * 32})
    with pytest.raises(UnknownKeyId):
        keys.key_for("v2")
