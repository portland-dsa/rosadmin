from __future__ import annotations

import logging

from cyclopts import App

logging.basicConfig(level=logging.INFO)

from workspace_sync.commands.one_shot import one_shot_app

app = App(
    name="workspace-sync",
    help="PDX DSA Google Workspace synchronization tool.",
)

app.command(one_shot_app)

# ---------------------------------------------------------------------------
# Future: web service
# ---------------------------------------------------------------------------
# @app.command
# def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
#     """Start the workspace-sync web service."""
#     ...
