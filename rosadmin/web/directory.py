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
from rosadmin.web.sessions import LeaderContext


class MemberDirectory(Protocol):
    """Reads and membership mutations, scoped to the acting leader."""

    async def search(self, email: str) -> SearchHit | SearchMiss: ...

    async def summaries_for(self, leader: LeaderContext) -> list[GroupSummary]: ...

    async def groups_for(self, leader: LeaderContext) -> list[Group]: ...

    async def add_member(
        self, leader: LeaderContext, group_id: UUID, member_id: UUID
    ) -> GroupMember: ...

    async def remove_member(
        self, leader: LeaderContext, group_id: UUID, member_id: UUID
    ) -> None: ...
