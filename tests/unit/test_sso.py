from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from rosadmin.sso import (
    BadSignature,
    Begun,
    DiscordUserId,
    Expired,
    MalformedAssertion,
    SigningKeys,
    SsoConfigError,
    SsoSettings,
    SsoUnreachable,
    Standing,
    UnknownKeyId,
    UnknownStanding,
    VerifiedAssertion,
    WrongAudience,
    WrongGuild,
    WrongIssuer,
    signing_keys_from_env,
    sso_begin,
    sso_complete,
    sso_settings_from_env,
    verify_assertion,
)
from tests.support.paseto import OMIT, keypair, sign_assertion


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


_SETTINGS = SsoSettings(
    iss="botonio",
    aud="rosadmin",
    kid="v1",
    guild_id="42",
    socket_path="/tmp/x.sock",
)
_NOW = datetime(2026, 7, 5, tzinfo=timezone.utc)


def test_verify_accepts_a_valid_member_assertion():
    signing_key, raw_pub = keypair()
    token = sign_assertion(signing_key, now=_NOW)
    result = verify_assertion(token, SigningKeys({"v1": raw_pub}), _SETTINGS, now=_NOW)
    assert result == VerifiedAssertion(
        discord_id=DiscordUserId("12345"),
        guild="42",
        standing=Standing.Member,
        jti="abc",
        exp=datetime.fromisoformat((_NOW + timedelta(seconds=30)).isoformat()),
    )


def test_verify_rejects_a_key_signed_by_a_stranger():
    stranger_key, _ = keypair()
    _, raw_pub = keypair()
    with pytest.raises(BadSignature):
        verify_assertion(
            sign_assertion(stranger_key, now=_NOW),
            SigningKeys({"v1": raw_pub}),
            _SETTINGS,
            now=_NOW,
        )


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"kid": "v9"}, UnknownKeyId),
        ({"iss": "not-botonio"}, WrongIssuer),
        ({"aud": "someone-else"}, WrongAudience),
        ({"guild": "999"}, WrongGuild),
        ({"standing": "banned"}, UnknownStanding),
    ],
)
def test_verify_rejects_bad_claims(overrides, error):
    signing_key, raw_pub = keypair()
    with pytest.raises(error):
        verify_assertion(
            sign_assertion(signing_key, now=_NOW, **overrides),
            SigningKeys({"v1": raw_pub}),
            _SETTINGS,
            now=_NOW,
        )


def test_verify_rejects_an_expired_assertion():
    signing_key, raw_pub = keypair()
    token = sign_assertion(
        signing_key, now=_NOW, exp=(_NOW - timedelta(seconds=1)).isoformat()
    )
    with pytest.raises(Expired):
        verify_assertion(token, SigningKeys({"v1": raw_pub}), _SETTINGS, now=_NOW)


@pytest.mark.parametrize("claim", ["exp", "nbf"])
def test_verify_rejects_a_naive_timestamp_claim(claim):
    # A well-formed botonio assertion always carries an offset; an offset-less
    # value must be refused inside the AssertionRejected tree rather than
    # reaching the tz-aware `now` comparison and raising a bare TypeError.
    signing_key, raw_pub = keypair()
    token = sign_assertion(signing_key, now=_NOW, **{claim: "2026-07-05T00:00:00"})
    with pytest.raises(MalformedAssertion):
        verify_assertion(token, SigningKeys({"v1": raw_pub}), _SETTINGS, now=_NOW)


def test_verify_selects_the_key_named_by_the_kid():
    # With two keys pinned, the token's kid picks exactly one. A token honestly
    # signed by key 2 under kid=v2 verifies; a token signed by key 1 but lying
    # that it is kid=v2 is checked against the v2 key and its signature fails.
    key_1, raw_pub_1 = keypair()
    key_2, raw_pub_2 = keypair()
    keys = SigningKeys({"v1": raw_pub_1, "v2": raw_pub_2})

    honest = sign_assertion(key_2, now=_NOW, kid="v2")
    assert (
        verify_assertion(honest, keys, _SETTINGS, now=_NOW).standing is Standing.Member
    )

    mismatched = sign_assertion(key_1, now=_NOW, kid="v2")
    with pytest.raises(BadSignature):
        verify_assertion(mismatched, keys, _SETTINGS, now=_NOW)


@pytest.mark.parametrize("missing", ["sub", "jti"])
def test_verify_rejects_an_assertion_missing_a_required_claim(missing):
    # A validly-signed but structurally incomplete payload stays inside the
    # AssertionRejected tree rather than escaping as a bare KeyError.
    signing_key, raw_pub = keypair()
    with pytest.raises(MalformedAssertion):
        verify_assertion(
            sign_assertion(signing_key, now=_NOW, **{missing: OMIT}),
            SigningKeys({"v1": raw_pub}),
            _SETTINGS,
            now=_NOW,
        )


def _token_with_payload(payload: bytes) -> str:
    body = base64.urlsafe_b64encode(payload + b"\x00" * 64).rstrip(b"=").decode()
    return f"v4.public.{body}"


@pytest.mark.parametrize("payload", [b"[1, 2, 3]", b"null", b"42", b'"hi"'])
def test_verify_rejects_a_non_object_payload(payload):
    # Valid JSON that is not an object must be refused at the pre-verification
    # peek, inside the AssertionRejected tree, not escape as an AttributeError.
    token = _token_with_payload(payload)
    with pytest.raises(MalformedAssertion):
        verify_assertion(token, SigningKeys({"v1": b"\x00" * 32}), _SETTINGS, now=_NOW)


@pytest.mark.parametrize("standing", ["dues_expired", "unverified", "not_in_guild"])
def test_verify_accepts_signed_non_member_standings(standing):
    # A non-member is a valid *signed* answer, not a rejection - the grant rule
    # (elsewhere) turns it into a denial.
    signing_key, raw_pub = keypair()
    result = verify_assertion(
        sign_assertion(signing_key, now=_NOW, standing=standing),
        SigningKeys({"v1": raw_pub}),
        _SETTINGS,
        now=_NOW,
    )
    assert result.standing is Standing(standing)


def test_signing_keys_from_env_reads_a_hex_key():
    _, raw_pub = keypair()
    keys = signing_keys_from_env({"BOTONIO_SSO_PUBLIC_KEY": raw_pub.hex()})
    assert keys.key_for("v1") == raw_pub


@pytest.mark.parametrize("value", ["", "nothex", "aa"])
def test_signing_keys_from_env_rejects_bad_keys(value):
    with pytest.raises(SsoConfigError):
        signing_keys_from_env({"BOTONIO_SSO_PUBLIC_KEY": value})


@respx.mock
async def test_sso_begin_returns_the_authorize_url():
    respx.post("http://botonio/sso/begin").mock(
        return_value=httpx.Response(
            200, json={"authorize_url": "https://d/x", "state": "s1"}
        )
    )
    assert await sso_begin(_SETTINGS, "bearer") == Begun(
        authorize_url="https://d/x", state="s1"
    )


@respx.mock
async def test_sso_complete_returns_the_assertion():
    respx.post("http://botonio/sso/complete").mock(
        return_value=httpx.Response(200, json={"assertion": "v4.public.xyz"})
    )
    assert await sso_complete(_SETTINGS, "bearer", "code", "s1") == "v4.public.xyz"


@respx.mock
async def test_sso_begin_raises_unreachable_on_a_non_json_200():
    respx.post("http://botonio/sso/begin").mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    with pytest.raises(SsoUnreachable):
        await sso_begin(_SETTINGS, "bearer")


@respx.mock
async def test_sso_complete_raises_unreachable_on_a_non_json_200():
    respx.post("http://botonio/sso/complete").mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    with pytest.raises(SsoUnreachable):
        await sso_complete(_SETTINGS, "bearer", "code", "s1")


@respx.mock
async def test_sso_complete_raises_unreachable_on_a_null_assertion():
    respx.post("http://botonio/sso/complete").mock(
        return_value=httpx.Response(200, json={"assertion": None})
    )
    with pytest.raises(SsoUnreachable):
        await sso_complete(_SETTINGS, "bearer", "code", "s1")


@respx.mock
async def test_sso_complete_raises_unreachable_on_a_non_string_assertion():
    respx.post("http://botonio/sso/complete").mock(
        return_value=httpx.Response(200, json={"assertion": 12345})
    )
    with pytest.raises(SsoUnreachable):
        await sso_complete(_SETTINGS, "bearer", "code", "s1")
