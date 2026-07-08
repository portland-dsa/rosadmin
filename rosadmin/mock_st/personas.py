"""The fabricated-member templates and their served `/users` records.

A `Persona` is a coherent membership state; its `user_json` output decodes through
the real Solidarity Tech decoder exactly as a live record would (pinned by the guard
test in `tests/unit/test_mock_st.py`). The decoder reads email, membership-status, names, the Discord id, the
chapter-leader flag, the leadership fields, and the alternate-email property;
the bot's `amber` and `email_verify` personas stay dropped, and no date or
dues properties are emitted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from rosadmin.membership.solidarity_tech.decode import STANDING_LABELS
from rosadmin.membership.solidarity_tech.fixtures import select_prop, status_prop
from rosadmin.membership.solidarity_tech.fixtures import user_json as _user_json
from rosadmin.membership.source import Standing
from rosadmin.mock_st.cast import identity_for

#: Base for a persona's synthetic Discord snowflake, so `discord-user-id` reads as a
#: plausible 18-digit id, deterministic in the record's ST id.
_DISCORD_ID_BASE = 900000000000000000


class Persona(Enum):
    """A named fabricated-member template."""

    GoodStanding = "good_standing"
    Lapsed = "lapsed"
    #: An unrecognized membership-status tier: a hard decode error, so the lenient
    #: sweep skips it (absent from the roster -> "not a member").
    RetiredTier = "retired_tier"
    #: No email: the strict decode rejects it as malformed, so the sweep skips it.
    Malformed = "malformed"
    #: No membership-status field at all - in the roster but with no standing (a form
    #: signup, an RSVP, a record that never updated); the decode raises and the sweep skips.
    NoStatus = "no_status"
    #: A garbage status label no tier has ever used: a hard decode error, like
    #: RetiredTier but for a value nobody has seen.
    UnknownTier = "unknown_tier"
    #: A member in good standing who leads groups; the fake-login identity.
    Leader = "leader"
    #: Shares Leader's committee seat, so two leaders can be attached to one body.
    CoLeader = "co_leader"
    #: The flag set with no leadership body behind it - the EmptyLeader anomaly.
    MarkedNoBody = "marked_no_body"
    #: A good-standing member whose primary is not a gmail but whose alternate-email
    #: property is - the sync_email rule's alternate-wins case.
    AltGmail = "alt_gmail"

    @classmethod
    def parse(cls, name: str) -> Persona | None:
        """The persona named by a map entry, or `None` for an unknown name."""
        try:
            return cls(name.strip())
        except ValueError:
            return None

    def user_json(
        self, st_id: int, email: str, discord_id: str | None = None
    ) -> dict[str, Any]:
        """This persona's served `/users` record. `Malformed` ignores `email`.

        `discord_id` replaces the synthetic snowflake when given - the staging
        path, where a record must match the Discord account a real SSO login
        hands back.
        """
        if self is Persona.Malformed:
            return _user_json(st_id, None, {})
        props: dict[str, Any] = {
            "discord-user-id": (
                discord_id if discord_id is not None else str(_DISCORD_ID_BASE + st_id)
            )
        }
        if self is not Persona.NoStatus:
            props["membership-status"] = status_prop(self._label())
        for field, label in _LEADS_BY_PERSONA.get(self, ()):
            props[field] = select_prop(label)
        if self in _CHAPTER_LEADER_PERSONAS:
            props["is-chapter-leader"] = select_prop("Yes")
        ident = identity_for(email)
        if _ALT_EMAIL_BY_PERSONA.get(self, False):
            props["alternate-email"] = f"{ident.handle}.drive@gmail.com"
        return _user_json(
            st_id,
            email,
            props,
            first_name=ident.first_name,
            last_name=ident.last_name,
            alternate_name=ident.alternate_name,
        )

    def _label(self) -> str:
        """The membership-status label this persona's record carries."""
        if self is Persona.RetiredTier:
            # A retired tier label -> decode raises, the lenient sweep skips.
            return "Lapsed Member"
        if self is Persona.UnknownTier:
            # A label no tier has ever used -> decode raises the unknown error.
            return "Certified Kromer Holder"
        return STANDING_LABELS[_STANDING_BY_PERSONA[self]]


#: The recognized standing each decoding persona resolves to. Keeps the
#: persona->standing correspondence explicit; the label strings live in STANDING_LABELS.
_STANDING_BY_PERSONA: dict[Persona, Standing] = {
    Persona.GoodStanding: Standing.GoodStanding,
    Persona.Lapsed: Standing.Lapsed,
    Persona.Leader: Standing.GoodStanding,
    Persona.CoLeader: Standing.GoodStanding,
    Persona.MarkedNoBody: Standing.GoodStanding,
    Persona.AltGmail: Standing.GoodStanding,
}

#: Leadership bodies each leadership persona holds, as (field, label) pairs.
#: Leader and CoLeader share the same committee seat; MarkedNoBody holds none,
#: pairing the flag below with an empty `leads` on purpose.
_LEADS_BY_PERSONA: dict[Persona, tuple[tuple[str, str], ...]] = {
    Persona.Leader: (("committee-leadership", "Steering"),),
    Persona.CoLeader: (("committee-leadership", "Steering"),),
    Persona.MarkedNoBody: (),
}

#: Personas whose record carries the is-chapter-leader flag set.
_CHAPTER_LEADER_PERSONAS: frozenset[Persona] = frozenset(
    {Persona.Leader, Persona.CoLeader, Persona.MarkedNoBody}
)

#: Personas whose record emits an alternate-email property, mirroring
#: _LEADS_BY_PERSONA's shape - the gmail itself is derived from the cast identity.
_ALT_EMAIL_BY_PERSONA: dict[Persona, bool] = {
    Persona.AltGmail: True,
}
