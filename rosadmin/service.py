"""The rosadmin web service: the leader-facing API behind systemd `Type=notify`.

The `create_app` factory assembles the `rosadmin.web` package - contract routes,
problem-details errors, the session dependency, and the Origin guard. The lifespan
turns a passing health check into the unit's readiness signal and pets the watchdog
on a timer, so an active unit is one that answered its own health check.

The development surface (persona stubs, fake-login) and the interactive docs mount
only behind a double gate: the fake-login setting on AND the rosadmin-devtools
package installed. Production carries neither, so they are absent, not merely off.

The operator admin app is a second, private FastAPI app assembled and served the
same lifespan, gated the same way: `ROSADMIN_ADMIN_SOCKET` set AND
rosadmin-admintools installed. It listens on its own unix socket rather than
sharing the public app's - who may reach that socket at all is decided entirely
by the systemd `RuntimeDirectory` that owns its directory (group membership,
file mode); this module never chmods the path or checks a peer credential.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from rosadmin import systemd_notify
from rosadmin.credentials import read_credential
from rosadmin.db import dsn_from_env, make_pool
from rosadmin.db.audit import AuditSink, PostgresAuditSink, RecordingAuditSink
from rosadmin.db.jti import PostgresJtiCache
from rosadmin.db.rate_limit import PostgresRateLimiter
from rosadmin.db.sessions import PostgresSessionStore
from rosadmin.group_sync import GroupSync, RecordingGroupSync, group_sync_from_env
from rosadmin.sso import SsoConfig, sso_config_from_env
from rosadmin.web.auth import auth_router, origin_guard
from rosadmin.web.jti import JtiCache
from rosadmin.web.problems import install_handlers
from rosadmin.web.rate_limit import InMemoryRateLimiter, RateLimiter
from rosadmin.web.records import RecordsDirectory, RecordsGroupModify
from rosadmin.web.routes import api_router
from rosadmin.web.sessions import SessionStore
from rosadmin.web.settings import WebSettings, settings_from_env

logger = logging.getLogger(__name__)

#: How long the lifespan waits for an auxiliary server (the admin socket, the
#: staging mock) to notice `should_exit` and finish serving before giving up
#: on a graceful stop and cancelling it.
_AUX_SHUTDOWN_TIMEOUT = 10.0


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _watchdog(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        systemd_notify.notify_watchdog()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Readiness is gated on the health check, so READY=1 means it passed. Explicit
    # check (not assert) so the readiness gate is not elided under `python -O`.
    if (await _healthz())["status"] != "ok":
        raise RuntimeError("startup health check failed")

    pool = None
    if getattr(app.state, "session_store", None) is None:
        pool = make_pool(dsn_from_env(os.environ))
        await pool.open()
        app.state.pool = pool
        app.state.session_store = PostgresSessionStore(pool)
        app.state.audit_sink = PostgresAuditSink(pool, _audit_key(os.environ))
        app.state.sso = sso_config_from_env(os.environ)
        app.state.jti_cache = PostgresJtiCache(pool)
        app.state.rate_limiter = PostgresRateLimiter(pool)
        app.state.directory = RecordsDirectory(pool)
        app.state.group_sync = group_sync_from_env(os.environ)
        app.state.group_modify = RecordsGroupModify(
            pool, app.state.group_sync, app.state.audit_sink
        )

    mock_st = await _start_mock_st(os.environ) if pool is not None else None
    admin = await _start_admin(pool, os.environ) if pool is not None else None

    systemd_notify.notify_ready()

    interval = systemd_notify.watchdog_interval()
    task = asyncio.create_task(_watchdog(interval)) if interval is not None else None
    try:
        yield
    finally:
        for name, aux in (("admin", admin), ("mock st", mock_st)):
            if aux is None:
                continue
            aux_server, aux_task = aux
            aux_server.should_exit = True
            try:
                await asyncio.wait_for(aux_task, timeout=_AUX_SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error("%s server did not stop in time, cancelling it", name)
                aux_task.cancel()
                with suppress(asyncio.CancelledError):
                    await aux_task
            except Exception:
                logger.error("%s server failed during shutdown", name, exc_info=True)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        group_modify = getattr(app.state, "group_modify", None)
        if group_modify is not None and hasattr(group_modify, "drain"):
            try:
                await asyncio.wait_for(group_modify.drain(), timeout=30)
            except asyncio.TimeoutError:
                logger.error("group mirror tasks did not drain in time")
        if pool is not None:
            await pool.close()


def _admin_socket_active(env: Mapping[str, str]) -> bool:
    """Both halves of the admin socket's double gate: the path configured AND
    the package present. Same shape as `_devtools_active`."""
    return (
        bool(env.get("ROSADMIN_ADMIN_SOCKET"))
        and importlib.util.find_spec("rosadmin_admintools") is not None
    )


def _mock_control_base(env: Mapping[str, str]) -> str | None:
    """The mock's control base URL when `SOLIDARITY_TECH_MOCK` names it as the
    configured source, mirroring `SolidarityTechClient.from_env`'s own toggle -
    so the admin app's persona relay is mounted exactly when the pull it
    triggers would itself read the mock."""
    return (
        env.get("SOLIDARITY_TECH_BASE_URL") if env.get("SOLIDARITY_TECH_MOCK") else None
    )


async def _start_mock_st(
    env: Mapping[str, str],
) -> tuple[Any, asyncio.Task[None]] | None:
    """Serve the in-process mock Solidarity Tech server when it is the source.

    botonio set the pattern: when `SOLIDARITY_TECH_MOCK` names
    the mock as the configured source, the service itself binds the mock to
    `SOLIDARITY_TECH_BASE_URL`'s host and port - one address the client reads
    and the mock serves, which cannot drift apart, and nothing extra to
    provision on the box. Production sets neither variable, so this is inert
    there; a base URL without an explicit host and port is refused rather than
    letting the mock bind somewhere the client would never call.
    """
    if not env.get("SOLIDARITY_TECH_MOCK"):
        return None
    base = env.get("SOLIDARITY_TECH_BASE_URL")
    if not base:
        raise RuntimeError(
            "SOLIDARITY_TECH_MOCK is set but SOLIDARITY_TECH_BASE_URL is not; "
            "refusing to guess where to bind the mock"
        )
    parsed = urlsplit(base)
    if parsed.hostname is None or parsed.port is None:
        raise RuntimeError(
            f"SOLIDARITY_TECH_BASE_URL ({base}) must carry an explicit "
            "host and port for the in-process mock to bind"
        )
    import uvicorn

    from rosadmin.mock_st.server import app_from_env

    logger.warning(
        "mock Solidarity Tech server active on %s (fabricated data only)", base
    )
    server = uvicorn.Server(
        uvicorn.Config(app_from_env(), host=parsed.hostname, port=parsed.port)
    )
    task = asyncio.create_task(server.serve())
    task.add_done_callback(_log_aux_task_failure)
    return server, task


async def _start_admin(
    pool: AsyncConnectionPool, env: Mapping[str, str]
) -> tuple[Any, asyncio.Task[None]] | None:
    """Serve the admin app on its own uvicorn.Server task when the double gate
    passes, else `None`. Split out of the lifespan so the gating is unit-testable
    without a running application or a bound socket.
    """
    if not _admin_socket_active(env):
        return None
    import uvicorn

    from rosadmin_admintools import create_admin_app

    admin_app = create_admin_app(pool, mock_control_base=_mock_control_base(env))
    server = uvicorn.Server(uvicorn.Config(admin_app, uds=env["ROSADMIN_ADMIN_SOCKET"]))
    task = asyncio.create_task(server.serve())
    task.add_done_callback(_log_aux_task_failure)
    return server, task


def _log_aux_task_failure(task: asyncio.Task[None]) -> None:
    """Surface an auxiliary server that crashed on its own - a stale socket
    file, a bad path, or an occupied port fails inside `server.serve()` itself,
    before anything in the lifespan would otherwise notice or log it."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("auxiliary server task failed", exc_info=error)


def _devtools_active(settings: WebSettings) -> bool:
    """Both halves of the double gate: the flag set AND the package present.

    Production artifacts contain neither, so the dev surface there is not disabled
    - it is GONE, and no configuration mistake can conjure it.
    """
    return (
        settings.fake_login_enabled
        and importlib.util.find_spec("rosadmin_devtools") is not None
    )


def _audit_key(env: Mapping[str, str]) -> bytes:
    """The audit HMAC key: a systemd credential on the box, env var in dev.

    Read through the shared [`read_credential`] from
    `$CREDENTIALS_DIRECTORY/audit-hmac-key` (how systemd delivers it) or
    `ROSADMIN_AUDIT_HMAC_KEY`. That the two delivery paths agree byte-for-byte
    matters here in particular: were a credential file's trailing newline to
    yield a different key than the same secret set inline, one actor's audit
    history would silently fork across two HMACs. The key is never logged.
    """
    raw = read_credential(env, "audit-hmac-key", "ROSADMIN_AUDIT_HMAC_KEY")
    if raw is None:
        raise RuntimeError("audit HMAC key is not configured")
    return raw.encode()


def create_app(
    settings: WebSettings | None = None,
    *,
    session_store: SessionStore | None = None,
    audit_sink: AuditSink | None = None,
    sso: SsoConfig | None = None,
    jti_cache: JtiCache | None = None,
    rate_limiter: RateLimiter | None = None,
    group_sync: GroupSync | None = None,
) -> FastAPI:
    """Assemble the service; the dev surface and docs sit behind the double gate.

    With no stores injected, the session store, audit sink, SSO config, jti
    cache, and rate limiter are all built from a Postgres pool and the environment
    in the lifespan (production). Tests inject the in-memory session fake, a
    `RecordingAuditSink`, and (when they exercise the login relay) an explicit
    `SsoConfig` and jti cache, so no database or real botonio socket is needed; the
    rate limiter defaults to an in-memory one so a test that does not care about
    limiting is never throttled by a shared Postgres counter.
    """
    if settings is None:
        settings = settings_from_env(os.environ)
    devtools = _devtools_active(settings)
    app = FastAPI(
        lifespan=_lifespan,
        docs_url="/api/docs" if devtools else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if devtools else None,
    )
    app.state.settings = settings
    app.state.pool = None
    app.state.directory = None
    app.state.group_sync = None
    app.state.group_modify = None
    if session_store is not None:
        app.state.session_store = session_store
        app.state.audit_sink = audit_sink or RecordingAuditSink()
        app.state.sso = sso
        app.state.jti_cache = jti_cache
        app.state.rate_limiter = rate_limiter or InMemoryRateLimiter()
        app.state.group_sync = group_sync or RecordingGroupSync()
    install_handlers(app)
    app.middleware("http")(origin_guard)
    app.include_router(api_router)
    app.include_router(auth_router)
    if devtools:
        # Imported inside the gate: the module does not exist in production.
        from rosadmin_devtools import StubDirectory, fake_login_router

        stub = StubDirectory()
        app.state.directory = stub
        app.state.group_modify = stub
        app.include_router(fake_login_router)
    return app


def contract_schema() -> dict[str, object]:
    """The published OpenAPI contract: the app assembled with the dev surface off.

    Dev-only routes (fake-login) are excluded, so the committed artifact is the
    stable surface the frontend builds against. Regenerate with
    `scripts/dump_openapi.py`.
    """
    return create_app(
        WebSettings(fake_login_enabled=False, allowed_origin=None)
    ).openapi()


app = create_app()
