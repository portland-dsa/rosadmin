from __future__ import annotations

import pytest

from rosadmin.membership.errors import DecodeError, MalformedMember
from rosadmin.membership.source import Standing
from rosadmin.membership.solidarity_tech.decode import decode_user
from rosadmin.membership.solidarity_tech.fixtures import status_prop, user_json


def test_good_standing_decodes():
    user = user_json(
        7,
        "kris@example.com",
        {"membership-status": status_prop("Member in Good Standing")},
    )
    m = decode_user(user)
    assert m.st_id == 7
    assert m.standing is Standing.GOOD_STANDING


def test_lapsed_decodes():
    user = user_json(
        8, "susie@example.com", {"membership-status": status_prop("Lapsed")}
    )
    assert decode_user(user).standing is Standing.LAPSED


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
