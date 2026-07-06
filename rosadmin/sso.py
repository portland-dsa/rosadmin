"""The botonio SSO protocol domain - configuration, assertion verification, and
the two socket calls.

rosadmin is a thin relay in front of the Discord bot's SSO endpoint: the bot
answers the single question "is this Discord user a Member right now?" with a
short-lived signed assertion, and rosadmin turns that into a Workspace login. The
full contract - the socket, the two endpoints, the assertion shape - is recorded
in `docs/sso-spec-report-from-botonio.md`.

This module holds everything below the HTTP relay handlers: the values rosadmin
validates an assertion against ([`SsoSettings`]), the bearer it authenticates to
the socket with ([`sso_bearer`]), the bot's verifying keys pinned by `kid`
([`SigningKeys`]), the PASETO verification that turns a raw token into typed
claims ([`verify_assertion`]), and the two authenticated socket calls
([`sso_begin`], [`sso_complete`]) plus the reachability probe built on the first
of them ([`check_reachable`]). The relay handlers that call these live in
[`rosadmin.web.auth`], the `jti` replay cache in [`rosadmin.web.jti`], and the
session mint in that callback; this module stays the protocol domain they build on.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

import httpx
import pyseto
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from rosadmin.credentials import read_credential

#: The issuer, audience, and signing-key version the bot stamps on a staging
#: assertion. They are fixed by the frozen contract, so they are defaults rather
#: than required configuration; a deployment overrides them only if the bot's
#: own values ever change.
DEFAULT_ISS = "botonio"
DEFAULT_AUD = "rosadmin"
DEFAULT_KID = "v1"


class SsoConfigError(Exception):
    """A required piece of the botonio SSO configuration is missing."""


@dataclass(frozen=True)
class SsoSettings:
    """What the relay validates a botonio assertion against, plus how to reach it.

    `iss`, `aud`, and `kid` carry the contract's fixed values as defaults (see
    the module constants), so an environment supplies in practice only the two
    things that actually vary: its home `guild_id` and the `socket_path` of the
    botonio it talks to. The socket path is deliberately required with no default
    - there is no universally-safe socket to fall back to, and forcing it to be
    explicit is what lets a test aim rosadmin at a throwaway socket without
    touching code.
    """

    iss: str
    aud: str
    kid: str
    guild_id: str
    socket_path: str


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise SsoConfigError(f"{key} is required but is not set")
    return value


def sso_settings_from_env(env: Mapping[str, str]) -> SsoSettings:
    """Read [`SsoSettings`] from the environment the deployment renders.

    The `BOTONIO_SSO_*` keys are kept distinct from the `ROSADMIN_*` family
    because they describe the external contract rosadmin talks to rather than
    rosadmin's own behavior. `guild_id` and `socket_path` are required; the rest
    default to the fixed contract values.
    """
    return SsoSettings(
        iss=env.get("BOTONIO_SSO_ISS", DEFAULT_ISS),
        aud=env.get("BOTONIO_SSO_AUD", DEFAULT_AUD),
        kid=env.get("BOTONIO_SSO_KID", DEFAULT_KID),
        guild_id=_require(env, "BOTONIO_SSO_GUILD_ID"),
        socket_path=_require(env, "BOTONIO_SSO_SOCKET_PATH"),
    )


def sso_bearer(env: Mapping[str, str]) -> str:
    """The shared secret rosadmin presents to the botonio socket.

    Read the way we read all our secrets - from
    `$CREDENTIALS_DIRECTORY/botonio_sso_bearer` on the box, else `BOTONIO_SSO_BEARER`
    in dev (see [`read_credential`]). It is the one genuinely-secret value in the
    SSO configuration and is never logged.
    """
    bearer = read_credential(env, "botonio_sso_bearer", "BOTONIO_SSO_BEARER")
    if bearer is None:
        raise SsoConfigError(
            "the botonio SSO bearer is not configured (neither the systemd "
            "credential botonio_sso_bearer nor BOTONIO_SSO_BEARER)"
        )
    return bearer


@dataclass(frozen=True)
class SigningKeys:
    """Botonio's verifying keys, pinned by `kid`, so rotation stays forward-only.

    An assertion's `kid` selects its verifying key through [`key_for`]; a `kid`
    absent from the map is refused with [`UnknownKeyId`]. Today the map is the
    single pair `{"v1": <the staging public key>}`; when the bot begins signing
    with `v2`, rosadmin gains the `v2` key here and keeps `v1` until the old
    assertions age out. A token never chooses its own verifier.
    The verification that consumes this lands with the relay; this is the
    holder it plugs onto.
    """

    by_kid: Mapping[str, bytes]

    def key_for(self, kid: str) -> bytes:
        """The public key pinned for `kid`, or [`UnknownKeyId`] if none is."""
        try:
            return self.by_kid[kid]
        except KeyError:
            raise UnknownKeyId(kid) from None


DiscordUserId = NewType("DiscordUserId", str)


class Standing(StrEnum):
    """The verdict botonio signs into an assertion. Only `MEMBER` authorizes."""

    MEMBER = "member"
    DUES_EXPIRED = "dues_expired"
    UNVERIFIED = "unverified"
    NOT_IN_GUILD = "not_in_guild"


class AssertionRejected(Exception):
    """A botonio assertion failed verification. The relay maps the whole tree to
    one uniform browser failure, so which check failed never leaks."""


class BadSignature(AssertionRejected):
    """The signature did not verify against any pinned key."""


class UnknownKeyId(AssertionRejected):
    """An assertion names a `kid` outside the configured, pinned set.

    A member of the [`AssertionRejected`] tree: an unpinned `kid` - a botonio
    key-rotation window, or forged/garbled input - fails verification,
    so the relay folds it into the same uniform
    browser failure as every other rejection. Raised rather than trusted -
    letting a token's own `kid` claim select a key rosadmin does not hold would
    let a rotated-away or forged key verify it.
    """

    def __init__(self, kid: str) -> None:
        super().__init__(f"no verifying key is pinned for kid {kid!r}")
        self.kid = kid


class WrongAudience(AssertionRejected):
    """The `aud` claim is not this relay's audience."""


class WrongIssuer(AssertionRejected):
    """The `iss` claim is not the configured issuer."""


class Expired(AssertionRejected):
    """`now` is outside the token's `nbf`..`exp` window."""


class WrongGuild(AssertionRejected):
    """The `guild` claim is not the configured home guild."""


class UnknownStanding(AssertionRejected):
    """The `standing` claim is not one of the four contract wire strings."""


class MalformedAssertion(AssertionRejected):
    """A signed assertion is missing or malformed a required claim.

    The signature checked out, but the payload is not a well-formed assertion -
    a required claim (`sub`, `jti`, or a timestamp) is absent, empty, or not the
    shape the contract promises.
    """


@dataclass(frozen=True)
class VerifiedAssertion:
    """A botonio assertion whose signature and registered claims all checked out.

    Proof of *authentication* - the signer vouches for this Discord id's standing
    at this instant. Not proof of access: the grant rule (`standing is
    Standing.MEMBER` and the guild) is a separate, visible decision at the relay.
    """

    discord_id: DiscordUserId
    guild: str
    standing: Standing
    jti: str
    exp: datetime


@dataclass(frozen=True)
class Begun:
    """A started login - botonio's authorize URL and the opaque `state` to carry
    through the Discord round-trip back to `sso_complete`."""

    authorize_url: str
    state: str


def _pyseto_key(raw: bytes) -> pyseto.KeyInterface:
    """Turn a raw 32-byte Ed25519 public key into a PySETO v4.public key.

    The contract publishes the key as hex; PySETO takes PEM, so we wrap the raw
    bytes in a SubjectPublicKeyInfo PEM via `cryptography`. Doing it here keeps
    `SigningKeys` a plain `kid -> raw bytes` map (its existing shape and tests).
    """
    pem = Ed25519PublicKey.from_public_bytes(raw).public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pyseto.Key.new(version=4, purpose="public", key=pem)


def _loads_object(data: bytes | str) -> dict[str, object]:
    """Parse `data` as a JSON *object*, or raise `MalformedAssertion`.

    Shared by the pre-verification kid peek and the verified payload so a payload
    that is valid JSON but not an object (an array, string, number, or `null`) is
    refused inside the `AssertionRejected` tree, rather than escaping as an
    `AttributeError` on a later `.get`.
    """
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as error:
        raise MalformedAssertion("assertion payload is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise MalformedAssertion("assertion payload is not a JSON object")
    return parsed


def _peek_kid(token: str) -> str:
    """The unverified `kid` claim, used to pick which pinned key verifies the token.

    A v4.public token is `v4.public.<b64url(message || signature)>[.<footer>]`; the
    message is the JSON claims and is readable before verification. Reading `kid`
    only selects among keys we already pin - a lying `kid` can name a key we hold or
    one we do not, never a key it controls - so the signature check that follows is
    still the authority. Selecting the one named key (rather than trying all) means a
    `kid`/key mismatch is caught, not silently accepted.
    """
    parts = token.split(".")
    if len(parts) < 3 or parts[0] != "v4" or parts[1] != "public":
        raise BadSignature("not a v4.public token")
    try:
        raw = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    except (ValueError, binascii.Error) as error:
        raise MalformedAssertion("assertion payload is not valid base64url") from error
    claims = _loads_object(raw[:-64])  # strip the 64-byte Ed25519 signature
    kid = claims.get("kid")
    if not isinstance(kid, str) or not kid:
        raise MalformedAssertion("assertion is missing its kid")
    return kid


def verify_assertion(
    token: str, keys: SigningKeys, settings: SsoSettings, *, now: datetime
) -> VerifiedAssertion:
    """Verify a botonio assertion completely, returning its typed claims.

    Selects the pinned key named by the token's `kid` and verifies the signature
    against only that key, then checks `iss`, `aud`, the `nbf`..`exp` window,
    `guild`, and parses `standing`. Every failure is a member of the
    [`AssertionRejected`] tree - including `UnknownKeyId` for an unpinned `kid`,
    so a caller that catches the tree catches this too. The grant rule is
    intentionally not decided here.
    """
    kid = _peek_kid(token)
    key = _pyseto_key(keys.key_for(kid))  # UnknownKeyId if kid is not pinned
    try:
        decoded = pyseto.decode(key, token)
    except Exception as error:  # PySETO raises VerifyError/DecryptError/ValueError
        raise BadSignature(str(error)) from error

    # No deserializer was configured, so PySETO hands back the raw payload bytes;
    # the dict branch only exists to satisfy its own broader `payload` type.
    payload = decoded.payload
    claims = _loads_object(payload) if isinstance(payload, bytes) else payload

    if claims.get("iss") != settings.iss:
        raise WrongIssuer("assertion issuer mismatch")
    if claims.get("aud") != settings.aud:
        raise WrongAudience("assertion audience mismatch")

    exp = _claim_time(claims, "exp")
    nbf = _claim_time(claims, "nbf") if "nbf" in claims else None
    if now >= exp or (nbf is not None and now < nbf):
        raise Expired("assertion outside its validity window")
    if claims.get("guild") != settings.guild_id:
        raise WrongGuild("assertion guild mismatch")
    try:
        standing = Standing(claims["standing"])
    except (KeyError, ValueError) as error:
        raise UnknownStanding(str(claims.get("standing"))) from error

    return VerifiedAssertion(
        discord_id=DiscordUserId(_required_claim(claims, "sub")),
        guild=str(claims["guild"]),
        standing=standing,
        jti=_required_claim(claims, "jti"),
        exp=exp,
    )


def _required_claim(claims: dict[str, object], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise MalformedAssertion(f"assertion {name} claim is missing or empty")
    return value


def _claim_time(claims: dict[str, object], name: str) -> datetime:
    value = claims.get(name)
    if not isinstance(value, str):
        raise MalformedAssertion(f"assertion {name} claim is missing or malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MalformedAssertion(
            f"assertion {name} claim is not a timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise MalformedAssertion(f"assertion {name} claim has no timezone offset")
    return parsed


def signing_keys_from_env(env: Mapping[str, str]) -> SigningKeys:
    """Pin botonio's verifying key by `kid` from the environment.

    Reads the hex public key from `BOTONIO_SSO_PUBLIC_KEY` and the `kid` it is
    pinned under (default `DEFAULT_KID`). Forward-only rotation adds keys here; a
    token never chooses its own verifier.
    """
    hex_key = env.get("BOTONIO_SSO_PUBLIC_KEY", "").strip()
    if not hex_key:
        raise SsoConfigError("BOTONIO_SSO_PUBLIC_KEY is required but is not set")
    try:
        raw = bytes.fromhex(hex_key)
    except ValueError as error:
        raise SsoConfigError("BOTONIO_SSO_PUBLIC_KEY is not valid hex") from error
    if len(raw) != 32:
        raise SsoConfigError("BOTONIO_SSO_PUBLIC_KEY is not a 32-byte Ed25519 key")
    return SigningKeys({env.get("BOTONIO_SSO_KID", DEFAULT_KID): raw})


@dataclass(frozen=True)
class SsoConfig:
    """Everything the relay needs to talk to botonio and verify its answers."""

    settings: SsoSettings
    bearer: str
    keys: SigningKeys


def sso_config_from_env(env: Mapping[str, str]) -> SsoConfig:
    """Assemble the whole SSO configuration from the environment and credentials."""
    return SsoConfig(
        settings=sso_settings_from_env(env),
        bearer=sso_bearer(env),
        keys=signing_keys_from_env(env),
    )


class SsoUnreachable(Exception):
    """A reachability probe could not complete a `/sso/begin` round-trip.

    Either the socket could not be reached at all, or the bot answered something
    other than a well-formed `200` - including its uniform `403`, whose cause it
    deliberately hides, so the message can only name the candidates.
    """


def _socket_client(settings: SsoSettings, bearer: str) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=settings.socket_path)
    return httpx.AsyncClient(
        transport=transport,
        timeout=10.0,
        headers={"Authorization": f"Bearer {bearer}"},
    )


async def sso_begin(settings: SsoSettings, bearer: str) -> Begun:
    """Start a login: one authenticated `POST /sso/begin`, returning botonio's
    authorize URL and its opaque `state`.

    The bearer is sent, never returned or logged. This runs only where the
    botonio socket exists (the box, some Linux, or WSL locally); on a machine
    without it the connection simply fails and surfaces as [`SsoUnreachable`].
    """
    try:
        async with _socket_client(settings, bearer) as client:
            response = await client.post("http://botonio/sso/begin")
    except httpx.HTTPError as error:
        raise SsoUnreachable(
            f"could not reach the botonio socket at {settings.socket_path}: {error}"
        ) from error
    if response.status_code == 403:
        raise SsoUnreachable(
            "botonio answered 403 (its uniform denial) - check the bearer, "
            "rosadmin's membership in the socket group, or that SSO is enabled "
            "for the guild"
        )
    if response.status_code != 200:
        raise SsoUnreachable(f"botonio answered an unexpected {response.status_code}")
    try:
        body = response.json()
        return Begun(authorize_url=body["authorize_url"], state=body["state"])
    except (KeyError, TypeError, ValueError) as error:
        # ValueError also covers json.JSONDecodeError, raised by response.json()
        # on a non-JSON 200 body.
        raise SsoUnreachable(
            "botonio answered 200 but with an unexpected body shape"
        ) from error


async def sso_complete(
    settings: SsoSettings, bearer: str, code: str, state: str
) -> str:
    """Redeem the Discord callback: one authenticated `POST /sso/complete`,
    returning the raw PASETO assertion. A `403` is a protocol failure only.

    The bearer is sent, never returned or logged.
    """
    try:
        async with _socket_client(settings, bearer) as client:
            response = await client.post(
                "http://botonio/sso/complete", json={"code": code, "state": state}
            )
    except httpx.HTTPError as error:
        raise SsoUnreachable(
            f"could not reach the botonio socket at {settings.socket_path}: {error}"
        ) from error
    if response.status_code != 200:
        raise SsoUnreachable(f"botonio answered {response.status_code} to complete")
    try:
        assertion = response.json()["assertion"]
    except (KeyError, TypeError, ValueError) as error:
        # ValueError also covers json.JSONDecodeError, raised by response.json()
        # on a non-JSON 200 body.
        raise SsoUnreachable("botonio complete answered an unexpected body") from error
    if not isinstance(assertion, str) or not assertion:
        raise SsoUnreachable("botonio complete answered a non-string assertion")
    return assertion


async def check_reachable(settings: SsoSettings, bearer: str) -> Begun:
    """Send one authenticated `POST /sso/begin` and confirm botonio answers 200.

    Proves the whole lower half of the contract - socket path, shared group,
    bearer, both enable switches - without minting a session. Returns what the bot
    answered; nothing there is secret (the authorize URL's `state` and PKCE
    challenge are single-use and the client id is public). Any failure surfaces as
    [`SsoUnreachable`].
    """
    return await sso_begin(settings, bearer)
