from __future__ import annotations

from typing import Any

import pytest

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.solidarity_tech.fixtures import (
    select_prop,
    status_prop,
    user_json,
)
from rosadmin.membership.source import BodyType, Leadership, Standing


def test_good_standing_decodes():
    user = user_json(
        7,
        "kris@example.com",
        {"membership-status": status_prop("Member in Good Standing")},
    )
    m = decode_user(user)
    assert m.st_id == 7
    assert m.standing is Standing.GoodStanding


def test_lapsed_decodes():
    user = user_json(
        8, "susie@example.com", {"membership-status": status_prop("Lapsed")}
    )
    assert decode_user(user).standing is Standing.Lapsed


def test_missing_email_is_malformed():
    user = user_json(
        9, None, {"membership-status": status_prop("Member in Good Standing")}
    )
    with pytest.raises(MalformedMember):
        decode_user(user)


def test_retired_tier_status_is_a_decode_error():
    user = user_json(
        10, "noelle@example.com", {"membership-status": status_prop("Lapsed Member")}
    )
    with pytest.raises(DecodeError):
        decode_user(user)


def test_missing_status_is_a_decode_error():
    user = user_json(11, "berdly@example.com", {})
    with pytest.raises(DecodeError):
        decode_user(user)


def _good_standing_props(**extra: Any) -> dict[str, Any]:
    return {"membership-status": status_prop("Member in Good Standing"), **extra}


def test_leads_decode_across_body_types_with_labels_verbatim():
    user = user_json(
        20,
        "kris@example.com",
        _good_standing_props(
            **{
                "committee-leadership": select_prop("Steering"),
                "working-group-leadership": select_prop("Dark World Research"),
            }
        ),
    )
    assert decode_user(user).leads == frozenset(
        {
            Leadership(BodyType.Committee, "Steering"),
            Leadership(BodyType.WorkingGroup, "Dark World Research"),
        }
    )


def test_leads_collects_several_entries_in_one_field():
    user = user_json(
        21,
        "susie@example.com",
        _good_standing_props(
            **{"committee-leadership": select_prop("Steering", "Outreach")}
        ),
    )
    assert decode_user(user).leads == frozenset(
        {
            Leadership(BodyType.Committee, "Steering"),
            Leadership(BodyType.Committee, "Outreach"),
        }
    )


@pytest.mark.parametrize("missing_field_value", [None, []])
def test_absent_or_empty_leadership_field_contributes_nothing(missing_field_value):
    props = _good_standing_props()
    if missing_field_value is not None:
        props["committee-leadership"] = missing_field_value
    user = user_json(22, "noelle@example.com", props)
    assert decode_user(user).leads == frozenset()


@pytest.mark.parametrize(
    "flag_value,expected",
    [
        (select_prop("Yes"), True),
        (select_prop("No"), False),
        (None, False),
        ([], False),
    ],
)
def test_chapter_leader_flag_decodes_from_affirmative_label(flag_value, expected):
    props = _good_standing_props()
    if flag_value is not None:
        props["is-chapter-leader"] = flag_value
    user = user_json(23, "berdly@example.com", props)
    assert decode_user(user).is_chapter_leader is expected


# A superscript-two digit passes str.isdigit() but int() rejects it.
@pytest.mark.parametrize(
    "discord_field_value", [None, "not-a-number", chr(0xB2), 12345]
)
def test_non_numeric_or_absent_discord_id_decodes_to_none(discord_field_value):
    props = _good_standing_props()
    if discord_field_value is not None:
        props["discord-user-id"] = discord_field_value
    user = user_json(24, "spamton@example.com", props)
    assert decode_user(user).discord_id is None


def test_discord_id_decodes_from_numeric_string():
    props = _good_standing_props(**{"discord-user-id": "900000000000000042"})
    user = user_json(25, "kris@example.com", props)
    assert decode_user(user).discord_id == 900000000000000042


def test_alternate_email_decodes_when_present():
    props = _good_standing_props(**{"alternate-email": "kris.drive@gmail.com"})
    user = user_json(26, "kris@example.com", props)
    assert decode_user(user).alternate_email == "kris.drive@gmail.com"


def test_alternate_email_absent_decodes_to_none():
    user = user_json(27, "susie@example.com", _good_standing_props())
    assert decode_user(user).alternate_email is None


@pytest.mark.parametrize("garbage", ["not-an-address", "", 12345])
def test_alternate_email_without_an_at_sign_decodes_to_none(garbage):
    props = _good_standing_props(**{"alternate-email": garbage})
    user = user_json(28, "noelle@example.com", props)
    assert decode_user(user).alternate_email is None
