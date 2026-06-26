from __future__ import annotations

import pytest

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.source import Standing
from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import parse_map, records


def test_parse_map_skips_blank_unknown_and_keyless_entries():
    parsed = parse_map(
        "kris@example.com=good_standing, susie@example.com=lapsed,,x@example.com=bogus,noequals"
    )
    assert parsed == [
        ("kris@example.com", Persona.GOOD_STANDING),
        ("susie@example.com", Persona.LAPSED),
    ]


def test_empty_map_is_an_empty_roster():
    assert records(parse_map("")) == []


def test_records_assign_sequential_ids_and_keep_the_key_email():
    rows = records(parse_map("kris@example.com=good_standing,susie@example.com=lapsed"))
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["email"] == "kris@example.com"
    assert rows[1]["email"] == "susie@example.com"


def test_malformed_persona_serves_a_null_email():
    rows = records(parse_map("spamton@example.com=malformed"))
    assert rows[0]["email"] is None


def test_personas_decode_to_their_intended_states():
    # The guard that the mock and the real decoder agree: good_standing and lapsed
    # decode to their Standing; retired_tier and malformed raise the errors the
    # lenient sweep skips.
    assert (
        decode_user(Persona.GOOD_STANDING.user_json(1, "kris@example.com")).standing
        is Standing.GOOD_STANDING
    )
    assert (
        decode_user(Persona.LAPSED.user_json(2, "susie@example.com")).standing
        is Standing.LAPSED
    )
    with pytest.raises(DecodeError):
        decode_user(Persona.RETIRED_TIER.user_json(3, "noelle@example.com"))
    with pytest.raises(MalformedMember):
        decode_user(Persona.MALFORMED.user_json(4, "spamton@example.com"))


def test_create_app_builds_a_fastapi_app():
    from fastapi import FastAPI

    from rosadmin.mock_st.server import create_app

    assert isinstance(create_app(parse_map("kris@example.com=good_standing")), FastAPI)
