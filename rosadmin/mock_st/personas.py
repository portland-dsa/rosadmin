"""The fabricated-member templates and their served `/users` records.

A `Persona` is a coherent membership state; its `user_json` output decodes through
the real Solidarity Tech decoder exactly as a live record would (pinned by the guard
test in `tests/unit/test_mock_st.py`). The decoder still reads only email and
membership-status, so the bot's `amber` and `email_verify` personas are dropped and
no date or dues properties are emitted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from rosadmin.membership.solidarity_tech.decode import STANDING_LABELS
from rosadmin.membership.solidarity_tech.fixtures import status_prop
from rosadmin.membership.solidarity_tech.fixtures import user_json as _user_json
from rosadmin.membership.source import Standing

#: Base for a persona's synthetic Discord snowflake, so `discord-user-id` reads as a
#: plausible 18-digit id, deterministic in the record's ST id.
_DISCORD_ID_BASE = 900000000000000000


class Persona(Enum):
    """A named fabricated-member template."""

    GOOD_STANDING = "good_standing"
    LAPSED = "lapsed"
    #: An unrecognized membership-status tier: a hard decode error, so the lenient
    #: sweep skips it (absent from the roster -> "not a member").
    RETIRED_TIER = "retired_tier"
    #: No email: the strict decode rejects it as malformed, so the sweep skips it.
    MALFORMED = "malformed"
    #: No membership-status field at all - in the roster but with no standing (a form
    #: signup, an RSVP, a record that never updated); the decode raises and the sweep skips.
    NO_STATUS = "no_status"
    #: A garbage status label no tier has ever used: a hard decode error, like
    #: RETIRED_TIER but for a value nobody has seen.
    UNKNOWN_TIER = "unknown_tier"
    #: A member in good standing who leads groups; the fake-login identity.
    LEADER = "leader"

    @classmethod
    def parse(cls, name: str) -> Persona | None:
        """The persona named by a map entry, or `None` for an unknown name."""
        try:
            return cls(name.strip())
        except ValueError:
            return None

    def user_json(self, st_id: int, email: str) -> dict[str, Any]:
        """This persona's served `/users` record. `MALFORMED` ignores `email`."""
        if self is Persona.MALFORMED:
            return _user_json(st_id, None, {})
        props: dict[str, Any] = {"discord-user-id": str(_DISCORD_ID_BASE + st_id)}
        if self is not Persona.NO_STATUS:
            props["membership-status"] = status_prop(self._label())
        first, last = _fixture_name(email)
        return _user_json(st_id, email, props, first_name=first, last_name=last)

    def _label(self) -> str:
        """The membership-status label this persona's record carries."""
        if self is Persona.RETIRED_TIER:
            # A retired tier label -> decode raises, the lenient sweep skips.
            return "Lapsed Member"
        if self is Persona.UNKNOWN_TIER:
            # A label no tier has ever used -> decode raises the unknown error.
            return "Certified Kromer Holder"
        return STANDING_LABELS[_STANDING_BY_PERSONA[self]]


def _fixture_name(email: str) -> tuple[str, str]:
    """A deterministic fabricated (first, last) name from the email's local part."""
    local = email.partition("@")[0]
    return (local.capitalize(), "Lightner")


#: The recognized standing each decoding persona resolves to. Keeps the
#: persona->standing correspondence explicit; the label strings live in STANDING_LABELS.
_STANDING_BY_PERSONA: dict[Persona, Standing] = {
    Persona.GOOD_STANDING: Standing.GOOD_STANDING,
    Persona.LAPSED: Standing.LAPSED,
    Persona.LEADER: Standing.GOOD_STANDING,
}
