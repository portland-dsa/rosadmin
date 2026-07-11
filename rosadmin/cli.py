from __future__ import annotations

import logging
import os
from typing import Annotated, Mapping

from cyclopts import App, Parameter

# Overridable via ROSADMIN_LOG_LEVEL (default INFO) so a run can be raised to
# DEBUG for diagnostics without a code change.
logging.basicConfig(level=os.environ.get("ROSADMIN_LOG_LEVEL", "INFO").upper())

from rosadmin.commands.one_shot import one_shot_app
from rosadmin.commands.roster import roster_app
from rosadmin.commands.sync import sync_app

app = App(
    name="rosadmin",
    help="PDX DSA Google Workspace synchronization tool.",
)

app.command(one_shot_app)
app.command(roster_app)
app.command(sync_app)


def _listen_target(env: Mapping[str, str]) -> int | None:
    """The inherited-socket fd under systemd socket activation, else None.

    systemd's convention: LISTEN_PID names the intended recipient and
    inherited fds start at 3. The PID check keeps a stray inherited
    environment from hijacking a child process's listener. Exactly one
    inherited socket is assumed - the socket unit declares a single
    ListenStream, and any extra fds would sit unserved, so adding a second
    listener means changing this function, not just the unit.
    """
    if env.get("LISTEN_PID") != str(os.getpid()):
        return None
    fds = env.get("LISTEN_FDS", "")
    if not fds.isdigit() or int(fds) < 1:
        return None
    return 3


@app.command
def serve(
    host: Annotated[
        str, Parameter(help="Bind address for local development.")
    ] = "127.0.0.1",
    port: Annotated[int, Parameter(help="TCP port for local development.")] = 8000,
    uds: Annotated[
        str | None, Parameter(help="Bind a unix socket path instead of TCP.")
    ] = None,
) -> None:
    """Run the rosadmin web service under uvicorn.

    Under systemd socket activation the listener is inherited and the
    host/port/uds flags are ignored; locally, --uds or host/port pick the
    listener.
    """
    import uvicorn

    fd = _listen_target(os.environ)
    if fd is None and uds is None and "NOTIFY_SOCKET" in os.environ:
        # Under systemd with no inherited socket, falling back to TCP would
        # quietly recreate the local port the socket unit exists to remove,
        # behind a service that still reports healthy. Refuse loudly.
        raise SystemExit(
            "running under systemd but no socket fd was inherited; "
            "refusing the TCP fallback (check LISTEN_PID/LISTEN_FDS delivery)"
        )
    # Forwarded headers are trusted only on unix-socket listeners. On the
    # inherited fd that trust is structural: the socket unit declares 0660
    # and the ingress group, so the reverse proxy is the only possible peer.
    # A bare --uds socket is a development convenience. Identity comes from the session
    # cookie on every path. On TCP, uvicorn's loopback-only default stands.
    if fd is not None:
        logging.info("listener: inherited socket fd %d", fd)
        uvicorn.run(
            "rosadmin.service:app", fd=fd, proxy_headers=True, forwarded_allow_ips="*"
        )
    elif uds is not None:
        logging.info("listener: unix socket %s", uds)
        uvicorn.run(
            "rosadmin.service:app", uds=uds, proxy_headers=True, forwarded_allow_ips="*"
        )
    else:
        logging.info("listener: http://%s:%d (local development)", host, port)
        uvicorn.run("rosadmin.service:app", host=host, port=port)


@app.command
def migrate() -> None:
    """Apply pending database migrations as the migration role.

    The unit runs this as an `ExecStartPre` so the schema is current before the
    service serves; a failure here aborts the start rather than serving a
    half-migrated database. It connects over TCP loopback with scram, the
    password taken from the `db_migration_password` systemd credential (or
    `ROSADMIN_DB_MIGRATE_PASSWORD` in dev).
    """
    from rosadmin.db.migrate import apply_pending, migrate_uri_from_env

    apply_pending(migrate_uri_from_env(os.environ))
