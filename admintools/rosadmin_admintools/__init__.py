"""The operator admin surface: body linking, a pull trigger, and (over the mock
only) a persona relay - served on a private unix socket named by
`ROSADMIN_ADMIN_SOCKET`.

This package is installed only where operator tooling belongs (never in a bare
production wheel) - the other half of the double-gate shape `rosadmin_devtools`
uses for the dev surface, gated in `rosadmin.service`.

There is no session or authentication machinery here: possession of the socket
is the authorization. Reaching the socket at all is the whole access-control
question, and that question is answered by the systemd `RuntimeDirectory`'s
owning group and file mode on the box, not by this code - nothing here checks
a peer credential or chmods the socket path.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from rosadmin.commands.roster import run_pull
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient

_UPDATE_LINK = """
    UPDATE leadership_bodies
    SET leader_google_group_email = %(leader)s, member_google_group_email = %(member)s
    WHERE id = %(body_id)s
"""

#: The mock's `/_control` routes the persona relay forwards to, one per admin route.
_RELAY_TARGETS: dict[str, tuple[str, str]] = {
    "set_member": ("PUT", "/_control/member"),
    "delete_member": ("DELETE", "/_control/member"),
    "set_standing": ("POST", "/_control/member/standing"),
    "set_leadership": ("POST", "/_control/member/leadership"),
}


class _Link(BaseModel):
    leader_email: str
    member_email: str


def create_admin_app(
    pool: AsyncConnectionPool, *, mock_control_base: str | None = None
) -> FastAPI:
    """Build the admin app over an already-open `pool`.

    The persona relay routes (`/admin/personas/*`) are mounted only when
    `mock_control_base` is truthy - absence (`None` or an empty string), not a
    403, when the configured membership source is the real Solidarity Tech
    API, since there is no mock to relay to.
    """
    app = FastAPI()

    @app.post("/admin/bodies/{body_id}/link", status_code=204)
    async def link(body_id: UUID, body: _Link) -> None:
        if not body.leader_email or not body.member_email:
            raise HTTPException(400, "both leader_email and member_email are required")
        await _set_link(pool, body_id, body.leader_email, body.member_email)

    @app.delete("/admin/bodies/{body_id}/link", status_code=204)
    async def unlink(body_id: UUID) -> None:
        await _set_link(pool, body_id, None, None)

    @app.post("/admin/roster/pull")
    async def pull() -> dict[str, object]:
        source = SolidarityTechClient.from_env(os.environ)
        try:
            report = await run_pull(pool, source)
        finally:
            await source.aclose()
        return {
            "members_upserted": report.members_upserted,
            "bodies_upserted": report.bodies_upserted,
            "leader_rows": report.leader_rows,
            "anomalies": len(report.anomalies),
            "skipped_st_ids": report.skipped_st_ids,
            "absent_lapsed": report.absent_lapsed,
            "lapse_refused": report.lapse_refused,
        }

    if mock_control_base:
        _mount_persona_relay(app, mock_control_base)

    return app


async def _set_link(
    pool: AsyncConnectionPool, body_id: UUID, leader: str | None, member: str | None
) -> None:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            _UPDATE_LINK, {"leader": leader, "member": member, "body_id": body_id}
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "unknown leadership body")


def _mount_persona_relay(app: FastAPI, base: str) -> None:
    """Thin relay onto the mock's `/_control` routes, forwarding the JSON body
    and mapping its status code straight through."""

    async def _relay(method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(base_url=base) as client:
            return await client.request(method, path, json=body)

    @app.put("/admin/personas/member")
    async def set_member(body: dict[str, Any]) -> dict[str, Any]:
        return await _forward("set_member", body)

    @app.delete("/admin/personas/member", status_code=204)
    async def delete_member(body: dict[str, Any]) -> None:
        await _forward("delete_member", body)

    @app.post("/admin/personas/standing")
    async def set_standing(body: dict[str, Any]) -> dict[str, Any]:
        return await _forward("set_standing", body)

    @app.post("/admin/personas/leadership")
    async def set_leadership(body: dict[str, Any]) -> dict[str, Any]:
        return await _forward("set_leadership", body)

    async def _forward(route: str, body: dict[str, Any]) -> dict[str, Any]:
        method, path = _RELAY_TARGETS[route]
        response = await _relay(method, path, body)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.text)
        return response.json() if response.content else {}
