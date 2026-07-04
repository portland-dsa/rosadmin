from __future__ import annotations

import logging
import os
from typing import Annotated, Mapping

from cyclopts import App, Parameter

logging.basicConfig(level=logging.INFO)

from rosadmin.commands.one_shot import one_shot_app

app = App(
    name="rosadmin",
    help="PDX DSA Google Workspace synchronization tool.",
)

app.command(one_shot_app)


def _listen_target(env: Mapping[str, str]) -> int | None:
    """The inherited-socket fd under systemd socket activation, else None.

    systemd's convention: LISTEN_PID names the intended recipient and
    inherited fds start at 3. The PID check keeps a stray inherited
    environment from hijacking a child process's listener.
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
    if fd is not None:
        uvicorn.run("rosadmin.service:app", fd=fd)
    elif uds is not None:
        uvicorn.run("rosadmin.service:app", uds=uds)
    else:
        uvicorn.run("rosadmin.service:app", host=host, port=port)
