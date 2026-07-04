"""The single definition of the Solidarity Tech `/users` wire shape.

Shared by the offline contract suite and (in a later plan) the persona mock, so
the two cannot drift - a wire-shape change breaks one place.
"""

from __future__ import annotations

from typing import Any


def status_prop(label: str) -> list[dict[str, str]]:
    """The membership-status select-field value carrying `label`: ``[{label, value}]``.

    The decode reads the label; the value is an opaque placeholder. The single owner of
    this select-field shape - the persona mock, the contract steps, and the decode unit
    tests all build it here, so the wire shape cannot drift between them.
    """
    return [{"label": label, "value": "mock"}]


def user_json(
    st_id: int,
    email: str | None,
    custom_props: dict[str, Any],
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    alternate_name: str | None = None,
) -> dict[str, Any]:
    """One `/users` user object; `email=None` emits a null email (a malformed record).

    `first_name`/`last_name`/`alternate_name` are the built-in top-level name
    fields. `alternate_name` is a chosen name: when present it is what to display
    in place of `first_name` (which may be a legal or dead name). rosadmin does not
    decode names yet - they ride the wire for staging fidelity.
    """
    return {
        "id": st_id,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "alternate_name": alternate_name,
        "phone_number": None,
        "custom_user_properties": custom_props,
    }


def users_page(
    users: list[dict[str, Any]], total_count: int, limit: int, offset: int
) -> dict[str, Any]:
    """Wrap `users` in the paginated list envelope the client reads."""
    return {
        "data": users,
        "meta": {"total_count": total_count, "limit": limit, "offset": offset},
    }
