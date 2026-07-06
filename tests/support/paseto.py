"""Shared PASETO fixtures for the botonio SSO tests: an Ed25519 keypair and a
signed assertion builder, used by both the pytest unit tests and the behave
login steps."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pyseto
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pyseto.key_interface import KeyInterface

# Passed as an override value to drop a claim entirely, so a caller can sign an
# assertion that is missing a claim rather than merely holding a wrong one.
OMIT = object()


def keypair() -> tuple[KeyInterface, bytes]:
    priv = Ed25519PrivateKey.generate()
    raw_pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pyseto.Key.new(4, "public", priv_pem), raw_pub


def sign_assertion(
    signing_key: KeyInterface, *, now: datetime | None = None, **overrides: object
) -> str:
    """Sign a botonio-shaped assertion. `now` anchors `nbf`/`exp` and defaults
    to the current time; any claim can be overridden, or dropped with `OMIT`."""
    anchor = now if now is not None else datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "iss": "botonio",
        "aud": "rosadmin",
        "kid": "v1",
        "sub": "12345",
        "guild": "42",
        "standing": "member",
        "jti": "abc",
        "nbf": anchor.isoformat(),
        "exp": (anchor + timedelta(seconds=30)).isoformat(),
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not OMIT}
    return pyseto.encode(signing_key, json.dumps(claims).encode()).decode()
