from __future__ import annotations

import pytest

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.source import BodyType, Leadership, Standing
from rosadmin.mock_st.personas import Persona
from rosadmin.mock_st.roster import RosterEntry, parse_map, records


def test_parse_map_skips_blank_unknown_and_keyless_entries():
    parsed = parse_map(
        "kris@example.com=good_standing, susie@example.com=lapsed,,x@example.com=bogus,noequals"
    )
    assert parsed == [
        RosterEntry("kris@example.com", Persona.GoodStanding),
        RosterEntry("susie@example.com", Persona.Lapsed),
    ]


def test_parse_map_reads_a_discord_id_override_and_skips_a_garbled_one():
    parsed = parse_map(
        "zoopgoop@example.com=leader:123456789012345678,spamton@example.com=leader:kromer"
    )
    assert parsed == [
        RosterEntry("zoopgoop@example.com", Persona.Leader, "123456789012345678")
    ]


def test_an_overridden_discord_id_lands_on_the_served_record():
    (row,) = records(parse_map("zoopgoop@example.com=leader:123456789012345678"))
    assert row["custom_user_properties"]["discord-user-id"] == "123456789012345678"


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
        decode_user(Persona.GoodStanding.user_json(1, "kris@example.com")).standing
        is Standing.GoodStanding
    )
    assert (
        decode_user(Persona.Lapsed.user_json(2, "susie@example.com")).standing
        is Standing.Lapsed
    )
    with pytest.raises(DecodeError):
        decode_user(Persona.RetiredTier.user_json(3, "noelle@example.com"))
    with pytest.raises(MalformedMember):
        decode_user(Persona.Malformed.user_json(4, "spamton@example.com"))


def test_new_personas_decode_to_their_intended_states():
    # Leader is an ordinary good-standing record to today's decoder; NoStatus and
    # UnknownTier raise the decode errors a lenient sweep skips.
    assert (
        decode_user(Persona.Leader.user_json(5, "ralsei@example.com")).standing
        is Standing.GoodStanding
    )
    with pytest.raises(DecodeError):
        decode_user(Persona.NoStatus.user_json(6, "berdly@example.com"))
    with pytest.raises(DecodeError):
        decode_user(Persona.UnknownTier.user_json(7, "spamton@example.com"))


def test_leader_persona_decodes_its_body_and_flag():
    member = decode_user(Persona.Leader.user_json(8, "ralsei@example.com"))
    assert member.is_chapter_leader is True
    assert member.leads == frozenset({Leadership(BodyType.Committee, "Steering")})


def test_co_leader_shares_the_leader_bodies():
    member = decode_user(Persona.CoLeader.user_json(9, "noelle@example.com"))
    assert member.is_chapter_leader is True
    assert member.leads == frozenset({Leadership(BodyType.Committee, "Steering")})


def test_marked_no_body_is_the_flag_with_no_leadership_anomaly():
    member = decode_user(Persona.MarkedNoBody.user_json(10, "berdly@example.com"))
    assert member.is_chapter_leader is True
    assert member.leads == frozenset()


def test_good_standing_persona_emits_no_leadership_fields():
    member = decode_user(Persona.GoodStanding.user_json(11, "kris@example.com"))
    assert member.is_chapter_leader is False
    assert member.leads == frozenset()


def test_alt_gmail_persona_decodes_a_gmail_alternate():
    member = decode_user(Persona.AltGmail.user_json(12, "kris@example.com"))
    assert member.standing is Standing.GoodStanding
    assert member.alternate_email == "kris.drive@gmail.com"


def test_records_carry_names_and_discord_id():
    record = Persona.GoodStanding.user_json(1, "kris@example.com")
    assert record["first_name"] == "Kris"
    assert record["last_name"] == "Dreemurr"
    assert record["alternate_name"] is None
    assert record["custom_user_properties"]["discord-user-id"] == "900000000000000001"


def test_create_app_builds_a_fastapi_app():
    from fastapi import FastAPI

    from rosadmin.mock_st.server import create_app

    assert isinstance(create_app(parse_map("kris@example.com=good_standing")), FastAPI)
