"""The request/response boundary of the leader-facing API."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """A member's standing within one group."""

    LEADER = "leader"
    MEMBER = "member"


class Member(BaseModel):
    """The searchable projection of a member in good standing."""

    id: UUID
    """Unique ID for the member. This will never change, even if we switch our membership records backend"""
    full_name: str
    """The member's full name, as derived by Solidarity Tech (note: preferred name is used over first name where applicable)"""
    email: str
    """The member's official email. Note, if their alternate email is a Gmail while their official one isn't, the account 
    with access may not match the one displayed!"""


class GroupMember(Member):
    """One row of a group's member list."""

    role: Role
    """The member's role in the *group* (leader or member)"""


class GroupSummary(BaseModel):
    """A group as named in the session overview. This is a lighter-weight Group without Membership Data"""

    id: UUID
    """Unique ID for that group. Will never be recycled, even if a WG disbands and restarts, the new one will have a new ID"""
    name: str
    """
    The group's name, without the suffix. For instance 'Steering Committee' is {'name': 'steering'}
    This is an open set, do not rely on values being consistent or predictable.
    """
    body_type: str
    """
    The group's type, without the name. For instance 'Steering Committee' is {'body_type': 'committee'}
    This is an open set, do not rely on values being consistent or predictable.
    """


class Group(GroupSummary):
    """One group the session's leader manages, members included."""

    members: list[GroupMember]
    """The members (and leaders) in this Group."""


class MeResponse(BaseModel):
    """Who is logged in and what they manage."""

    display_name: str
    """This user's display name, as of now, always the same as their "full name" if queried as a member."""
    groups: list[GroupSummary]
    """A list of all Groups (that the logged in leader manages) this member belongs to or leads."""


class SearchRequest(BaseModel):
    """Exact-email member search. POST keeps the address out of URLs and logs."""

    email: str
    """Their *primary membership email*."""


class SearchHit(BaseModel):
    """The one search outcome that discloses a record: an addable member."""

    status: Literal["good_standing"]
    member: Member


class SearchMiss(BaseModel):
    """Every no-record outcome, distinguished so the leader knows who to ask.

    Extras are forbidden: a miss must never carry a member object, so it can
    never be mistaken for a hit.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["dues_expired", "no_membership_status", "malformed", "not_found"]
    """
    `dues_expired`: known lapsed. 
    `no_membership_status`: the backend has the email but no standing at all (may not be a member). 
    `malformed`: a member-shaped record in a definitely-broken state. 
    `not_found`: no record.
    """


SearchResponse = Annotated[Union[SearchHit, SearchMiss], Field(discriminator="status")]


class AddMemberRequest(BaseModel):
    """Add one member (by our UUID) to the path's group."""

    member_id: UUID
    """The member's unique ID. This will never change, even if a new membership records platform is chosen."""


class FakeLoginRequest(BaseModel):
    """Development/staging login: impersonate a named persona."""

    persona: str
    """The name of the persona to invoke or attempt a login as"""
