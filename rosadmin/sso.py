"""The botonio SSO integration - the caller side's configuration and a reachability probe.

rosadmin is a thin relay in front of the Discord bot's SSO endpoint: the bot
answers the single question "is this Discord user a Member right now?" with a
short-lived signed assertion, and rosadmin turns that into a Workspace login. The
full contract - the socket, the two endpoints, the assertion shape - is recorded
in `docs/sso-spec-report-from-botonio.md`.

This module holds the pieces that sit *below* the relay: the values rosadmin
validates an assertion against ([`SsoSettings`]), the bearer it authenticates to
the socket with ([`sso_bearer`]), the bot's verifying keys pinned by `kid`
([`SigningKeys`]), and a probe that proves the socket, the shared group, and the
bearer are wired before the relay that uses them exists ([`check_reachable`]).
The relay itself - the begin/callback handlers, the PASETO verification that
consumes [`SigningKeys`], and the session mint - lands later and is not here yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx

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


class UnknownKeyId(Exception):
    """An assertion names a `kid` outside the configured, pinned set.

    Raised rather than trusted: letting a token's own `kid` claim select a key
    rosadmin does not hold would let a rotated-away or forged key verify it. The
    pinned map is the whole point.
    """

    def __init__(self, kid: str) -> None:
        super().__init__(f"no verifying key is pinned for kid {kid!r}")
        self.kid = kid


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


@dataclass(frozen=True)
class Reachable:
    """A completed `/sso/begin` round-trip - the endpoint is wired and answering.

    Carries what the bot returned so a caller can eyeball it. Nothing here is
    secret: the authorize URL's `state` and PKCE challenge are single-use and the
    client id is public.
    """

    authorize_url: str
    state: str


class SsoUnreachable(Exception):
    """A reachability probe could not complete a `/sso/begin` round-trip.

    Either the socket could not be reached at all, or the bot answered something
    other than a well-formed `200` - including its uniform `403`, whose cause it
    deliberately hides, so the message can only name the candidates.
    """


async def check_reachable(settings: SsoSettings, bearer: str) -> Reachable:
    """Send one authenticated `POST /sso/begin` and confirm the bot answers `200`.

    Proves the whole lower half of the contract at once - the socket path is
    right, the shared group grants access, the bearer is accepted, and both of
    the bot's enable switches are on - without minting a session or completing a
    login. Any outcome but a well-formed `200` raises [`SsoUnreachable`].

    The bearer is sent, never returned or logged. This runs only where the
    botonio socket exists (the box, ~some linux~, or WSL locally); on a machine without it the
    connection simply fails and surfaces as [`SsoUnreachable`].
    """
    transport = httpx.AsyncHTTPTransport(uds=settings.socket_path)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            response = await client.post(
                "http://botonio/sso/begin",
                headers={"Authorization": f"Bearer {bearer}"},
            )
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

    body = response.json()
    try:
        return Reachable(authorize_url=body["authorize_url"], state=body["state"])
    except (KeyError, TypeError) as error:
        raise SsoUnreachable(
            "botonio answered 200 but with an unexpected body shape"
        ) from error
