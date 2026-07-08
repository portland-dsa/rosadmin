"""The membership-source port and the typed `Member` it yields.

The backend is reached only through `MembershipSource`, so a future swap from
Solidarity Tech to another source (BigQuery, Action Network) changes one adapter
and nothing that consumes members.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType, Protocol, runtime_checkable

Email = NewType("Email", str)
"""An email address that has crossed a decode or row-mapping boundary."""


def sync_email(primary: Email, alternate: Email | None) -> Email:
    """The address Google-side membership targets.

    The primary when it is a gmail; else the alternate when that is a gmail
    (the Drive-capable account members record when their primary has none);
    else the primary. Display and search always use the primary - this rule
    governs only the Google mirror.
    """
    if _is_gmail(primary):
        return primary
    if alternate is not None and _is_gmail(alternate):
        return alternate
    return primary


def _is_gmail(address: Email) -> bool:
    return address.lower().endswith("@gmail.com")


class Standing(Enum):
    """A member's decoded membership standing.

    Only the two states a real, projectable member can hold. Records that cannot
    be decoded into one of these are not `Member`s at all - they surface as a
    `DecodeError` or `MalformedMember` instead, keeping illegal states out of the
    type.
    """

    GoodStanding = "good_standing"
    Lapsed = "lapsed"


class BodyType(Enum):
    """The kind of body a leadership role belongs to.

    The value is the human-readable string the API serves as the open
    `body_type` field - a leaf client renders it as opaque text, so a value
    outside this set is expected elsewhere and must not break decoding here.
    """

    Committee = "Committee"
    WorkingGroup = "Working Group"
    Branch = "Branch"
    Campaign = "Campaign"
    Misc = "Misc"


@dataclass(frozen=True)
class Leadership:
    """One leadership role: which kind of body, and its name."""

    body_type: BodyType
    name: str


@dataclass(frozen=True)
class Member:
    """One membership record, projected from the backend onto a stable shape.

    Carries identity (`st_id`, `email`), standing, the display-name parts and
    Discord id, and the leadership state: the raw `is_chapter_leader` flag next
    to `leads`, the bodies derived from the leadership fields. Dues expiry and
    group belonging beyond leadership are not modeled yet.

    `alternate_email` is a member-supplied second address, read only for
    `sync_email` - display and search always use `email`.
    """

    st_id: int
    email: Email
    alternate_email: Email | None
    standing: Standing
    discord_id: int | None
    first_name: str | None
    last_name: str | None
    alternate_name: str | None
    is_chapter_leader: bool
    leads: frozenset[Leadership]


class LeadershipAssessment(Enum):
    """The cross-check between the raw chapter-leader flag and the derived roles."""

    Leader = "leader"
    NonLeader = "non_leader"
    UnmarkedLeader = "unmarked_leader"
    EmptyLeader = "empty_leader"

    @property
    def is_anomalous(self) -> bool:
        """True when the raw chapter-leader flag and the derived roles disagree -
        the states an operator is warned about and the login gate refuses."""
        return self in _ANOMALOUS_ASSESSMENTS


_ANOMALOUS_ASSESSMENTS = frozenset(
    {LeadershipAssessment.UnmarkedLeader, LeadershipAssessment.EmptyLeader}
)

#: The operator warning for an anomalous assessment, formatted with the internal
#: member UUID and the assessment value (never PII); shared by the pull and gate.
ANOMALY_WARNING = (
    "member %s assessed %s: is-chapter-leader and leadership roles disagree"
)


def assess(
    is_chapter_leader: bool, leads: frozenset[Leadership]
) -> LeadershipAssessment:
    """Cross-check the raw chapter-leader flag against the derived roles.

    The two are meant to agree (an ST automation keeps them in step); a
    disagreement is a data bug the caller warns on. Only `Leader` authorizes.
    """
    has_bodies = len(leads) > 0
    if is_chapter_leader and has_bodies:
        return LeadershipAssessment.Leader
    if not is_chapter_leader and not has_bodies:
        return LeadershipAssessment.NonLeader
    if is_chapter_leader:  # flag set, no bodies
        return LeadershipAssessment.EmptyLeader
    return LeadershipAssessment.UnmarkedLeader  # bodies, flag unset


@runtime_checkable
class MembershipSource(Protocol):
    """The swappable backend port: a whole-roster read and a targeted lookup.

    `list_members` is the lenient sweep - it skips any record that fails to
    decode. `find_by_email` is targeted - it returns `None` when nobody matches,
    skips a record with no email, and surfaces any other decode failure.
    """

    async def list_members(self) -> list[Member]: ...

    async def find_by_email(self, email: str) -> Member | None: ...
