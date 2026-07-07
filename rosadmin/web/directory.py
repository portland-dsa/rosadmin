"""The directory ports the resource routes consume: reads and mutations, split
so a build can wire real reads while mutations still answer a stable 501."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from rosadmin.web.models import (
    Group,
    GroupMember,
    GroupSummary,
    SearchHit,
    SearchMiss,
)
from rosadmin.web.sessions import Principal


class MemberDirectory(Protocol):
    """Reads, scoped to the acting principal."""

    async def search(self, email: str) -> SearchHit | SearchMiss: ...

    async def display_name_for(self, principal: Principal) -> str: ...

    async def summaries_for(self, principal: Principal) -> list[GroupSummary]: ...

    async def groups_for(self, principal: Principal) -> list[Group]: ...


class GroupModify(Protocol):
    """Membership mutations, scoped to the acting principal."""

    async def add_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> GroupMember: ...

    async def remove_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> None: ...
