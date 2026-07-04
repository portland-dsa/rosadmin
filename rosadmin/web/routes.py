"""The leader-facing resource routes. Every handler demands a LeaderContext."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request

from rosadmin.web.auth import require_leader
from rosadmin.web.directory import MemberDirectory
from rosadmin.web.models import (
    AddMemberRequest,
    Group,
    GroupMember,
    MeResponse,
    SearchRequest,
    SearchResponse,
)
from rosadmin.web.sessions import LeaderContext

api_router = APIRouter(prefix="/api")


def _directory(request: Request) -> MemberDirectory:
    return request.app.state.directory


@api_router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/me", response_model=MeResponse)
async def me(
    request: Request, leader: LeaderContext = Depends(require_leader)
) -> MeResponse:
    directory = _directory(request)
    return MeResponse(
        display_name=leader.display_name,
        groups=await directory.summaries_for(leader),
    )


@api_router.get("/me/groups", response_model=list[Group])
async def my_groups(
    request: Request, leader: LeaderContext = Depends(require_leader)
) -> list[Group]:
    return await _directory(request).groups_for(leader)


# A POST, not a GET with a query string: the searched email is member PII and must
# never enter URLs, access logs, or history. Revisit if the HTTP QUERY method (safe
# method with a body) lands with real ecosystem support.
@api_router.post("/members/search", response_model=SearchResponse)
async def search_members(
    request: Request,
    body: SearchRequest,
    leader: LeaderContext = Depends(require_leader),
) -> SearchResponse:
    return await _directory(request).search(body.email)


@api_router.post(
    "/groups/{group_id}/members", response_model=GroupMember, status_code=201
)
async def add_member(
    request: Request,
    group_id: Annotated[UUID, Path(description="The group's unique ID.")],
    body: AddMemberRequest,
    leader: LeaderContext = Depends(require_leader),
) -> GroupMember:
    return await _directory(request).add_member(leader, group_id, body.member_id)


@api_router.delete("/groups/{group_id}/members/{member_id}", status_code=204)
async def remove_member(
    request: Request,
    group_id: Annotated[UUID, Path(description="The group's unique ID.")],
    member_id: Annotated[
        UUID, Path(description="The member's unique ID within that group.")
    ],
    leader: LeaderContext = Depends(require_leader),
) -> None:
    await _directory(request).remove_member(leader, group_id, member_id)
