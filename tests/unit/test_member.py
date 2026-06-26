from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from rosadmin.membership.source import Member, MembershipSource, Standing
from rosadmin.membership.errors import DecodeError, MalformedMember, MembershipError


def test_member_is_frozen_and_carries_standing():
    m = Member(
        st_id=1,
        email="susie@example.com",
        standing=Standing.GOOD_STANDING,
    )
    assert m.standing is Standing.GOOD_STANDING
    assert m.email == "susie@example.com"
    with pytest.raises(FrozenInstanceError):
        setattr(m, "email", "other@example.com")


def test_error_tree_is_rooted():
    assert issubclass(MalformedMember, MembershipError)
    assert issubclass(DecodeError, MembershipError)


def test_source_is_a_protocol():
    # A class with the two async methods structurally satisfies the port.
    class Fake:
        async def list_members(self):
            return []

        async def find_by_email(self, email):
            return None

    assert isinstance(Fake(), MembershipSource)


def test_client_satisfies_the_port():
    from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
    from rosadmin.membership.source import MembershipSource

    assert isinstance(SolidarityTechClient(token="x"), MembershipSource)
