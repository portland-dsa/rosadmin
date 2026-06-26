"""The single definition of the Solidarity Tech `/users` wire shape.

Shared by the offline contract suite and (in a later plan) the persona mock, so
the two cannot drift - a wire-shape change breaks one place.
"""

from __future__ import annotations

from typing import Any


def user_json(
    st_id: int, email: str | None, custom_props: dict[str, Any]
) -> dict[str, Any]:
    """One `/users` user object; `email=None` emits a null email (a malformed record)."""
    return {
        "id": st_id,
        "email": email,
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
