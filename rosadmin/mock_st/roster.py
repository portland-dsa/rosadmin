"""Parse the `SOLIDARITY_TECH_MOCK_PERSONAS` map and build the served roster.

The map is `<email>=persona,...`, where a value may also carry a Discord id
override as `persona:<discord-id>`. The key is the email stamped onto the
record (ignored for `MALFORMED`, which serves a null email). A blank entry is
skipped; an entry with no `=`, an unknown persona, or a non-numeric Discord id
is warned and skipped, so one typo never sinks the whole map.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rosadmin.mock_st.personas import Persona

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RosterEntry:
    """One persona-map entry: the record's email, its template, and - when the
    map names one - the real Discord id the record carries in place of a
    synthetic snowflake. The override is what lets a staging tester log in
    through the real SSO and still match their fabricated record, since the
    login gate joins on the pulled `discord-user-id`."""

    email: str
    persona: Persona
    discord_id: str | None = None


def parse_map(raw: str) -> list[RosterEntry]:
    """Parse `"kris@x=good_standing,zoopgoop@x=leader:123456789012345678"` entries."""
    out: list[RosterEntry] = []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        key, sep, value = entry.partition("=")
        if not sep:
            logger.warning("mock persona map: entry %r has no '='", entry)
            continue
        name, id_sep, raw_id = value.partition(":")
        persona = Persona.parse(name)
        if persona is None:
            logger.warning("mock persona map: unknown persona in %r", entry)
            continue
        discord_id = raw_id.strip() if id_sep else None
        if discord_id is not None and not is_snowflake(discord_id):
            logger.warning("mock persona map: non-numeric discord id in %r", entry)
            continue
        out.append(
            RosterEntry(email=key.strip(), persona=persona, discord_id=discord_id)
        )
    return out


def is_snowflake(raw: str) -> bool:
    """True when `raw` parses as an integer Discord id.

    Parse-or-refuse rather than `isdigit`, which accepts Unicode digits
    `int()` rejects. Shared by the map parser and the mock's live upsert."""
    try:
        int(raw)
    except ValueError:
        return False
    return True


def records(parsed: list[RosterEntry]) -> list[dict[str, Any]]:
    """Build the served `/users` records, assigning sequential Solidarity Tech ids."""
    return [
        entry.persona.user_json(i + 1, entry.email, discord_id=entry.discord_id)
        for i, entry in enumerate(parsed)
    ]
