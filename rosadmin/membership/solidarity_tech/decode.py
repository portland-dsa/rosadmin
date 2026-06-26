"""Project one Solidarity Tech `/users` user object onto a typed `Member`.

Pure and side-effect-free: the same rules the live client applies, factored out
so they can be unit-tested without a network. The membership-status custom
property is a select field - a list of `{label, value}` - and the label is what
carries meaning; the value is an opaque placeholder.
"""

from __future__ import annotations

from typing import Any

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.source import Member, Standing

#: Recognized membership-status labels. Anything else (e.g. a retired tier like
#: "Lapsed Member") is a `DecodeError`, so the lenient sweep skips it and a
#: targeted lookup reports it.
_STANDING_BY_LABEL: dict[str, Standing] = {
    "Member in Good Standing": Standing.GOOD_STANDING,
    "Lapsed": Standing.LAPSED,
}


def decode_user(user: dict[str, Any]) -> Member:
    """Decode one user object, raising `MalformedMember` (no email) or `DecodeError`."""
    email = user.get("email")
    if not email:
        raise MalformedMember(f"user {user.get('id')!r} has no email")

    props = user.get("custom_user_properties") or {}
    return Member(
        st_id=int(user["id"]),
        email=email,
        standing=_decode_standing(props),
    )


def _decode_standing(props: dict[str, Any]) -> Standing:
    field = props.get("membership-status")
    label = field[0].get("label") if isinstance(field, list) and field else None
    if label is None:
        raise DecodeError("membership-status missing or malformed")
    try:
        return _STANDING_BY_LABEL[label]
    except KeyError:
        raise DecodeError(f"unrecognized membership status {label!r}") from None
