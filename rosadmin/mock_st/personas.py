"""The fabricated-member templates and their served `/users` records.

A `Persona` is a coherent membership state; its `user_json` output decodes through
the real Solidarity Tech decoder exactly as a live record would (pinned by the
guard test in `tests/unit/test_mock_st.py`). rosadmin keys members by email and
models neither dues expiry nor Discord identity, so the bot's `amber` and
`email_verify` personas are dropped and no date or Discord-id properties are emitted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from rosadmin.membership.solidarity_tech.decode import STANDING_LABELS
from rosadmin.membership.solidarity_tech.fixtures import status_prop
from rosadmin.membership.solidarity_tech.fixtures import user_json as _user_json
from rosadmin.membership.source import Standing


class Persona(Enum):
    """A named fabricated-member template."""

    GOOD_STANDING = "good_standing"
    LAPSED = "lapsed"
    #: An unrecognized membership-status tier: a hard decode error, so the lenient
    #: sweep skips it (absent from the roster -> "not a member").
    RETIRED_TIER = "retired_tier"
    #: No email: the strict decode rejects it as malformed, so the sweep skips it.
    MALFORMED = "malformed"

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
        return _user_json(
            st_id, email, {"membership-status": status_prop(self._label())}
        )

    def _label(self) -> str:
        """The membership-status label this persona's record carries."""
        if self is Persona.RETIRED_TIER:
            # A deliberately unrecognized label -> decode raises, the lenient sweep skips.
            return "Lapsed Member"
        return STANDING_LABELS[_STANDING_BY_PERSONA[self]]


#: The recognized standing each non-malformed, non-retired persona decodes to. Keeps the
#: persona->standing correspondence explicit; the label strings live in STANDING_LABELS.
_STANDING_BY_PERSONA: dict[Persona, Standing] = {
    Persona.GOOD_STANDING: Standing.GOOD_STANDING,
    Persona.LAPSED: Standing.LAPSED,
}
