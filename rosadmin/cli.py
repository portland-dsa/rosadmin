from __future__ import annotations

import logging

from cyclopts import App

logging.basicConfig(level=logging.INFO)

from rosadmin.commands.one_shot import one_shot_app

app = App(
    name="rosadmin",
    help="PDX DSA Google Workspace synchronization tool.",
)

app.command(one_shot_app)


@app.command
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the rosadmin web service under uvicorn."""
    import uvicorn

    uvicorn.run("rosadmin.service:app", host=host, port=port)
