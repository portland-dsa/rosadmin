"""Parse the `SOLIDARITY_TECH_MOCK_PERSONAS` map and build the served roster.

The map is `<email>=persona,...`. The key is the email stamped onto the record
(ignored for `MALFORMED`, which serves a null email). A blank entry is skipped; an
entry with no `=` or an unknown persona is warned and skipped, so one typo never
sinks the whole map.
"""

from __future__ import annotations

import logging
from typing import Any

from rosadmin.mock_st.personas import Persona

logger = logging.getLogger(__name__)


def parse_map(raw: str) -> list[tuple[str, Persona]]:
    """Parse `"kris@x=good_standing,susie@x=lapsed"` into `(email, persona)` pairs."""
    out: list[tuple[str, Persona]] = []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        key, sep, name = entry.partition("=")
        if not sep:
            logger.warning("mock persona map: entry %r has no '='", entry)
            continue
        persona = Persona.parse(name)
        if persona is None:
            logger.warning("mock persona map: unknown persona in %r", entry)
            continue
        out.append((key.strip(), persona))
    return out


def records(parsed: list[tuple[str, Persona]]) -> list[dict[str, Any]]:
    """Build the served `/users` records, assigning sequential Solidarity Tech ids."""
    return [
        persona.user_json(i + 1, email) for i, (email, persona) in enumerate(parsed)
    ]
