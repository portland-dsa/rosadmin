"""The member-directory port the resource routes consume."""

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
    """Reads and membership mutations, scoped to the acting principal."""

    async def search(self, email: str) -> SearchHit | SearchMiss: ...

    async def display_name_for(self, principal: Principal) -> str: ...

    async def summaries_for(self, principal: Principal) -> list[GroupSummary]: ...

    async def groups_for(self, principal: Principal) -> list[Group]: ...

    async def add_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> GroupMember: ...

    async def remove_member(
        self, principal: Principal, group_id: UUID, member_id: UUID
    ) -> None: ...
