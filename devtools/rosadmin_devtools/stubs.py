"""Contract-shaped fake data, derived from the personas.

The fixture roster shares its display names with the mock backend through the
cast (`identity_for`), the served IDs are deterministic UUIDs so client-side
fixtures survive restarts, and mutations really mutate (in memory, reset on
restart) so the add/remove flows and their error paths are exercisable end to
end. The real data path replaces this module without changing anything a client
sees.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rosadmin.mock_st.cast import identity_for
from rosadmin.mock_st.personas import Persona
from rosadmin.sso import DiscordUserId
from rosadmin.web.models import (
    Group,
    GroupMember,
    GroupSummary,
    Member,
    Role,
    SearchHit,
    SearchMiss,
)
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.sessions import Principal

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://rosadmin.invalid/stub")


def _member_id(email: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"member:{email}")


def _group_id(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"group:{name}")


def _stub_discord_id(email: str) -> str:
    # A stub stand-in for the Discord id, used only by the local fake-login surface
    # to key a principal back to its persona. Deliberately not a real-looking
    # snowflake - nothing here ever talks to Discord.
    return f"fake-discord-{email}"


@dataclass(frozen=True)
class _Person:
    email: str
    persona: Persona

    @property
    def full_name(self) -> str:
        """The display name, resolved through the shared cast so it matches the ST records."""
        return identity_for(self.email).full_name


#: The fixture roster: who exists, in which membership state. Names come from the
#: shared cast, so a persona reads the same here and in the served ST records.
#: MALFORMED is absent on purpose - it has no email, so no email search can reach it.
_ROSTER: tuple[_Person, ...] = (
    _Person("ralsei@example.com", Persona.LEADER),
    _Person("kris@example.com", Persona.GOOD_STANDING),
    _Person("susie@example.com", Persona.LAPSED),
    _Person("noelle@example.com", Persona.RETIRED_TIER),
    _Person("berdly@example.com", Persona.NO_STATUS),
    _Person("spamton@example.com", Persona.UNKNOWN_TIER),
)

#: Persona -> search status. Total over the roster's personas; the guard test pins
#: totality so a new persona cannot silently fall through to not_found.
_STATUS_BY_PERSONA: dict[Persona, str] = {
    Persona.LEADER: "good_standing",
    Persona.GOOD_STANDING: "good_standing",
    Persona.LAPSED: "dues_expired",
    Persona.NO_STATUS: "no_membership_status",
    Persona.RETIRED_TIER: "malformed",
    Persona.UNKNOWN_TIER: "malformed",
}

#: name, body_type, seeded member emails. One body_type is deliberately a value
#: outside any preconceived taxonomy: clients must render body_type as opaque text.
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Castle Town", "Committee", ("kris@example.com",)),
    ("Dark World Research", "Working Group", ()),
    ("Fun Gang Reunion", "Campaign", ()),
)

_LEADER_EMAIL = "ralsei@example.com"


class StubDirectory:
    """The persona-backed `MemberDirectory`, plus the fake-login identity map."""

    def __init__(self) -> None:
        self._people = {p.email: p for p in _ROSTER}
        self._by_discord = {_stub_discord_id(p.email): p for p in _ROSTER}
        self._group_meta = {
            _group_id(name): (name, body_type) for name, body_type, _ in _GROUPS
        }
        leader = self._people[_LEADER_EMAIL]
        self._members: dict[uuid.UUID, dict[uuid.UUID, GroupMember]] = {}
        for name, _body_type, seeded in _GROUPS:
            gid = _group_id(name)
            rows = {
                _member_id(leader.email): self._entry(leader, Role.LEADER),
            }
            for email in seeded:
                person = self._people[email]
                rows[_member_id(person.email)] = self._entry(person, Role.MEMBER)
            self._members[gid] = rows

    def _entry(self, person: _Person, role: Role) -> GroupMember:
        return GroupMember(
            id=_member_id(person.email),
            full_name=person.full_name,
            email=person.email,
            role=role,
        )

    def principal_for(self, persona_name: str) -> Principal:
        person = next((p for p in _ROSTER if p.persona.value == persona_name), None)
        if person is None:
            raise AppProblem(404, ProblemCode.UNKNOWN_PERSONA, "no such persona")
        if person.persona is not Persona.LEADER:
            raise AppProblem(
                403, ProblemCode.NOT_CHAPTER_LEADER, "persona is not a chapter leader"
            )
        return Principal(discord_id=DiscordUserId(_stub_discord_id(person.email)))

    def _person(self, principal: Principal) -> _Person:
        person = self._by_discord.get(principal.discord_id)
        if person is None:
            raise AppProblem(404, ProblemCode.NOT_FOUND, "unknown principal")
        return person

    def _managed_ids(self, principal: Principal) -> frozenset[uuid.UUID]:
        # The stub leader manages every seeded group; a non-leader manages none.
        person = self._person(principal)
        return (
            frozenset(self._group_meta)
            if person.persona is Persona.LEADER
            else frozenset()
        )

    async def search(self, email: str) -> SearchHit | SearchMiss:
        person = self._people.get(email)
        if person is None:
            return SearchMiss(status="not_found")
        status = _STATUS_BY_PERSONA[person.persona]
        if status == "good_standing":
            return SearchHit(
                status="good_standing",
                member=Member(
                    id=_member_id(person.email),
                    full_name=person.full_name,
                    email=person.email,
                ),
            )
        return SearchMiss.model_validate({"status": status})

    async def display_name_for(self, principal: Principal) -> str:
        return self._person(principal).full_name

    async def summaries_for(self, principal: Principal) -> list[GroupSummary]:
        managed = self._managed_ids(principal)
        return [
            GroupSummary(id=gid, name=name, body_type=body_type)
            for gid, (name, body_type) in self._group_meta.items()
            if gid in managed
        ]

    async def groups_for(self, principal: Principal) -> list[Group]:
        return [
            Group(
                id=summary.id,
                name=summary.name,
                body_type=summary.body_type,
                members=sorted(
                    self._members[summary.id].values(), key=lambda m: m.full_name
                ),
            )
            for summary in await self.summaries_for(principal)
        ]

    def _managed_group(self, principal: Principal, group_id: uuid.UUID) -> None:
        # Unknown and not-yours answer identically: the API does not confirm the
        # existence of what a session cannot touch.
        if (
            group_id not in self._managed_ids(principal)
            or group_id not in self._members
        ):
            raise AppProblem(404, ProblemCode.NOT_FOUND, "no such group")

    async def add_member(
        self, principal: Principal, group_id: uuid.UUID, member_id: uuid.UUID
    ) -> GroupMember:
        self._managed_group(principal, group_id)
        person = next((p for p in _ROSTER if _member_id(p.email) == member_id), None)
        if person is None or _STATUS_BY_PERSONA[person.persona] != "good_standing":
            # Only members surfaced by a good-standing search hit are addable;
            # everyone else is indistinguishable from nonexistent.
            raise AppProblem(404, ProblemCode.MEMBER_NOT_FOUND, "no such member")
        if member_id in self._members[group_id]:
            raise AppProblem(409, ProblemCode.ALREADY_MEMBER, "already a member")
        entry = self._entry(person, Role.MEMBER)
        self._members[group_id][member_id] = entry
        return entry

    async def remove_member(
        self, principal: Principal, group_id: uuid.UUID, member_id: uuid.UUID
    ) -> None:
        self._managed_group(principal, group_id)
        if member_id not in self._members[group_id]:
            raise AppProblem(
                404, ProblemCode.NOT_A_MEMBER, "not a member of this group"
            )
        del self._members[group_id][member_id]
