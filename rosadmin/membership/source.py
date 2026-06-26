"""The membership-source port and the typed `Member` it yields.

The backend is reached only through `MembershipSource`, so a future swap from
Solidarity Tech to another source (BigQuery, Action Network) changes one adapter
and nothing that consumes members.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class Standing(Enum):
    """A member's decoded membership standing.

    Only the two states a real, projectable member can hold. Records that cannot
    be decoded into one of these are not `Member`s at all - they surface as a
    `DecodeError` or `MalformedMember` instead, keeping illegal states out of the
    type.
    """

    GOOD_STANDING = "good_standing"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class Member:
    """One membership record, projected from the backend onto a stable shape.

    Deliberately the minimum the sweep and lookup need: an identity and a
    standing. Dues expiry, leadership status, and group belonging are not modeled
    yet - a future decode slice will add what the reconcile actually consumes.
    """

    st_id: int
    email: str
    standing: Standing


@runtime_checkable
class MembershipSource(Protocol):
    """The swappable backend port: a whole-roster read and a targeted lookup.

    `list_members` is the lenient sweep - it skips any record that fails to
    decode. `find_by_email` is targeted - it returns `None` when nobody matches,
    skips a record with no email, and surfaces any other decode failure.
    """

    async def list_members(self) -> list[Member]: ...

    async def find_by_email(self, email: str) -> Member | None: ...
