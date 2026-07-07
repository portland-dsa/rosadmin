"""Project one Solidarity Tech `/users` user object onto a typed `Member`.

Pure and side-effect-free: the same rules the live client applies, factored out
so they can be unit-tested without a network. The membership-status custom
property is a select field - a list of `{label, value}` - and the label is what
carries meaning; the value is an opaque placeholder.
"""

from __future__ import annotations

from typing import Any

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.source import BodyType, Leadership, Member, Standing

#: The canonical membership-status label for each recognized standing. The single owner
#: of these label strings: the decode reverse-maps them and the persona mock builds its
#: records from them, so the two cannot drift.
STANDING_LABELS: dict[Standing, str] = {
    Standing.GoodStanding: "Member in Good Standing",
    Standing.Lapsed: "Lapsed",
}

#: Reverse of `STANDING_LABELS`. Anything not here (e.g. a retired tier like "Lapsed
#: Member") is a `DecodeError`, so the lenient sweep skips it and a targeted lookup
#: reports it.
_STANDING_BY_LABEL: dict[str, Standing] = {
    label: standing for standing, label in STANDING_LABELS.items()
}


#: Each leadership select field, mapped to the body type its entries describe.
#: The single owner of this field-name -> BodyType correspondence.
_FIELD_BODY_TYPES: dict[str, BodyType] = {
    "committee-leadership": BodyType.Committee,
    "working-group-leadership": BodyType.WorkingGroup,
    "branch-leadership": BodyType.Branch,
    "campaign-leadership": BodyType.Campaign,
    "misc-leaderish-roles": BodyType.Misc,
}

#: The label Solidarity Tech stores in the `is-chapter-leader` select field for a yes.
_CHAPTER_LEADER_TRUE = "Yes"


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
        discord_id=_decode_discord_id(props),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        alternate_name=user.get("alternate_name"),
        is_chapter_leader=_decode_chapter_leader(props),
        leads=_decode_leads(props),
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


def _decode_leads(props: dict[str, Any]) -> frozenset[Leadership]:
    leads: set[Leadership] = set()
    for field, body_type in _FIELD_BODY_TYPES.items():
        entries = props.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            label = entry.get("label") if isinstance(entry, dict) else None
            if isinstance(label, str) and len(label) > 0:
                leads.add(Leadership(body_type=body_type, name=label))
    return frozenset(leads)


def _decode_chapter_leader(props: dict[str, Any]) -> bool:
    # A yes/no select field. Solidarity Tech encodes false as null or [] and true as
    # an entry labelled "Yes"; keying on that affirmative label rather than mere
    # presence means an explicit "No" entry, were one ever stored, reads as false.
    field = props.get("is-chapter-leader")
    if not isinstance(field, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("label") == _CHAPTER_LEADER_TRUE
        for entry in field
    )


def _decode_discord_id(props: dict[str, Any]) -> int | None:
    # `str.isdigit()` is True for non-decimal Unicode digits (superscripts, circled
    # digits) that `int()` rejects, so parse-or-None instead of guarding on isdigit.
    raw = props.get("discord-user-id")
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None
