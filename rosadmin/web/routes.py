"""The leader-facing resource routes. Every handler demands a directory and a
session, in that order - see `require_directory`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request

from rosadmin.web.auth import require_session
from rosadmin.web.directory import MemberDirectory
from rosadmin.web.models import (
    AddMemberRequest,
    Group,
    GroupMember,
    MeResponse,
    SearchRequest,
    SearchResponse,
)
from rosadmin.web.problems import AppProblem, ProblemCode
from rosadmin.web.sessions import Principal

api_router = APIRouter(prefix="/api")


def require_directory(request: Request) -> MemberDirectory:
    """The stub/records directory, or 501 when reads are not wired in this build.

    Deployed builds carry no directory yet, so the resource routes answer a stable
    501 there; a local build with devtools serves stub data. Placed before the
    session dependency so the answer does not depend on being logged in.
    """
    directory = request.app.state.directory
    if directory is None:
        raise AppProblem(
            501, ProblemCode.READS_NOT_AVAILABLE, "reads are not yet available"
        )
    return directory


@api_router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/me", response_model=MeResponse)
async def me(
    directory: MemberDirectory = Depends(require_directory),
    principal: Principal = Depends(require_session),
) -> MeResponse:
    return MeResponse(
        display_name=await directory.display_name_for(principal),
        groups=await directory.summaries_for(principal),
    )


@api_router.get("/me/groups", response_model=list[Group])
async def my_groups(
    directory: MemberDirectory = Depends(require_directory),
    principal: Principal = Depends(require_session),
) -> list[Group]:
    return await directory.groups_for(principal)


# A POST, not a GET with a query string: the searched email is member PII and must
# never enter URLs, access logs, or history. Revisit if the HTTP QUERY method (safe
# method with a body) lands with real ecosystem support.
@api_router.post("/members/search", response_model=SearchResponse)
async def search_members(
    body: SearchRequest,
    directory: MemberDirectory = Depends(require_directory),
    principal: Principal = Depends(require_session),
) -> SearchResponse:
    # search does not scope by principal (unused), but still demands a live session.
    return await directory.search(body.email)


@api_router.post(
    "/groups/{group_id}/members", response_model=GroupMember, status_code=201
)
async def add_member(
    group_id: Annotated[UUID, Path(description="The group's unique ID.")],
    body: AddMemberRequest,
    directory: MemberDirectory = Depends(require_directory),
    principal: Principal = Depends(require_session),
) -> GroupMember:
    return await directory.add_member(principal, group_id, body.member_id)


@api_router.delete("/groups/{group_id}/members/{member_id}", status_code=204)
async def remove_member(
    group_id: Annotated[UUID, Path(description="The group's unique ID.")],
    member_id: Annotated[
        UUID, Path(description="The member's unique ID within that group.")
    ],
    directory: MemberDirectory = Depends(require_directory),
    principal: Principal = Depends(require_session),
) -> None:
    await directory.remove_member(principal, group_id, member_id)
