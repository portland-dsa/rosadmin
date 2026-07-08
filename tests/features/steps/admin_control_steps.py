# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Callable
from uuid import uuid4

import httpx
import uvicorn
from behave import given, then, when

from rosadmin.db import make_pool
from rosadmin.membership.source import Standing
from rosadmin.mock_st.roster import parse_map
from rosadmin.mock_st.server import create_app as create_mock_app
from rosadmin_admintools import create_admin_app

BASE = "http://admin.test"


def _start_mock_server(spec: str) -> tuple[str, Callable[[], None]]:
    """Bind the controllable mock to a real ephemeral loopback port and serve it
    on a background thread for the scenario's duration - `SolidarityTechClient`
    and the persona relay both speak real HTTP, so a bound port (not an
    ASGITransport) is what stands in for the mock service here. Returns the
    base URL and a teardown callable a step registers with `context.add_cleanup`.
    """
    app = create_mock_app(parse_map(spec), controllable=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    def run_loop() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)

    return f"http://127.0.0.1:{port}", stop


def _admin_call(context, method: str, path: str, **kwargs):
    async def go():
        pool = make_pool(context.db.app_dsn)
        await pool.open()
        try:
            app = create_admin_app(
                pool, mock_control_base=getattr(context, "mock_base", None)
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                return await client.request(method, path, **kwargs)
        finally:
            await pool.close()

    return asyncio.run(go())


def _query(dsn, sql_text, params=()):
    async def go():
        pool = make_pool(dsn)
        await pool.open()
        try:
            async with pool.connection() as conn:
                cursor = await conn.execute(sql_text, params)
                return await cursor.fetchall()
        finally:
            await pool.close()

    return asyncio.run(go())


@given('the admin socket is pointed at a mock roster "{spec}"')
def step_admin_mock_roster(context, spec):
    base, stop = _start_mock_server(spec)
    context.mock_base = base
    context.add_cleanup(stop)
    os.environ["SOLIDARITY_TECH_MOCK"] = "1"
    os.environ["SOLIDARITY_TECH_BASE_URL"] = base
    context.add_cleanup(os.environ.pop, "SOLIDARITY_TECH_MOCK", None)
    context.add_cleanup(os.environ.pop, "SOLIDARITY_TECH_BASE_URL", None)


@when(
    'Ralsei links "{body}" through the admin socket with leader group '
    '"{leader}" and member group "{member}"'
)
def step_link(context, body, leader, member):
    body_id = context.bodies[body]
    context.admin_response = _admin_call(
        context,
        "POST",
        f"/admin/bodies/{body_id}/link",
        json={"leader_email": leader, "member_email": member},
    )
    context.body_emails[body] = (leader, member)


@when(
    "Ralsei links a nonexistent body through the admin socket with leader "
    'group "{leader}" and member group "{member}"'
)
def step_link_unknown(context, leader, member):
    context.admin_response = _admin_call(
        context,
        "POST",
        f"/admin/bodies/{uuid4()}/link",
        json={"leader_email": leader, "member_email": member},
    )


@when('Ralsei unlinks "{body}" through the admin socket')
def step_unlink(context, body):
    body_id = context.bodies[body]
    context.admin_response = _admin_call(
        context, "DELETE", f"/admin/bodies/{body_id}/link"
    )


@when("Ralsei triggers a pull through the admin socket")
def step_admin_pull(context):
    context.admin_response = _admin_call(context, "POST", "/admin/roster/pull")


@when('Ralsei expires "{email}" through the persona relay')
def step_relay_expire(context, email):
    context.admin_response = _admin_call(
        context,
        "POST",
        "/admin/personas/standing",
        json={"email": email, "standing": "lapsed"},
    )


@then("the admin response is {status:d}")
def step_admin_status(context, status):
    assert context.admin_response.status_code == status, context.admin_response.text


@then('the body "{body}" is unlinked')
def step_body_unlinked(context, body):
    rows = _query(
        context.db.app_dsn,
        "SELECT leader_google_group_email, member_google_group_email "
        "FROM leadership_bodies WHERE id = %s",
        (context.bodies[body],),
    )
    assert rows == [(None, None)], rows


@then('"{email}" has standing "{standing}"')
def step_has_standing(context, email, standing):
    ((actual,),) = _query(
        context.db.app_dsn, "SELECT standing FROM members WHERE email = %s", (email,)
    )
    assert actual is Standing(standing), actual
