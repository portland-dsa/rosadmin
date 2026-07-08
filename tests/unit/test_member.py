from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rosadmin.membership.errors import DecodeError, MalformedMember, MembershipError
from rosadmin.membership.source import (
    BodyType,
    Email,
    Leadership,
    LeadershipAssessment,
    Member,
    MembershipSource,
    Standing,
    assess,
    sync_email,
)


def test_member_is_frozen_and_carries_standing():
    m = Member(
        st_id=1,
        email=Email("susie@example.com"),
        alternate_email=None,
        standing=Standing.GoodStanding,
        discord_id=None,
        first_name="Susie",
        last_name="Gaster",
        alternate_name=None,
        is_chapter_leader=False,
        leads=frozenset(),
    )
    assert m.standing is Standing.GoodStanding
    assert m.email == "susie@example.com"
    with pytest.raises(FrozenInstanceError):
        setattr(m, "email", "other@example.com")


@pytest.mark.parametrize(
    "primary,alternate,expected",
    [
        (Email("susie@gmail.com"), Email("noelle@gmail.com"), "susie@gmail.com"),
        (Email("susie@portlanddsa.org"), Email("noelle@gmail.com"), "noelle@gmail.com"),
        (
            Email("susie@portlanddsa.org"),
            Email("noelle@example.com"),
            "susie@portlanddsa.org",
        ),
        (Email("susie@portlanddsa.org"), None, "susie@portlanddsa.org"),
    ],
)
def test_sync_email_prefers_a_gmail_primary_then_a_gmail_alternate(
    primary, alternate, expected
):
    assert sync_email(primary, alternate) == expected


_BODY = frozenset({Leadership(BodyType.Committee, "Steering")})


@pytest.mark.parametrize(
    "flag,leads,expected",
    [
        (True, _BODY, LeadershipAssessment.Leader),
        (False, frozenset(), LeadershipAssessment.NonLeader),
        (False, _BODY, LeadershipAssessment.UnmarkedLeader),
        (True, frozenset(), LeadershipAssessment.EmptyLeader),
    ],
)
def test_assess_covers_the_grid(flag, leads, expected):
    assert assess(flag, leads) == expected


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
