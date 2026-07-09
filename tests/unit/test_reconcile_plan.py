"""The plan function's edges: case, protection, and the fuse."""

from __future__ import annotations

from uuid import uuid4

import pytest

from rosadmin.google_group import GroupMemberEntry, GroupsPermissionLevel
from rosadmin.membership.source import Email
from rosadmin.reconcile import plan_group


def _user(
    email: str,
    permission_level: GroupsPermissionLevel = GroupsPermissionLevel.Member,
    type: str | None = "USER",
) -> GroupMemberEntry:
    return GroupMemberEntry(
        email=email, permission_level=permission_level, status="ACTIVE", type=type
    )


GROUP = Email("steering-members@example.net")
MEMBER_ID = uuid4()


@pytest.mark.parametrize(
    ("desired", "actual", "expect_adds", "expect_removes", "expect_refused"),
    [
        # converged: Ralsei is wanted and already present, nothing to do
        ({"ralsei@example.net": MEMBER_ID}, [_user("ralsei@example.net")], (), (), 0),
        # case-insensitive match: the same Ralsei, cased differently, is no change
        ({"ralsei@example.net": MEMBER_ID}, [_user("Ralsei@Example.NET")], (), (), 0),
        # Susie is wanted by no one and gets removed
        ({}, [_user("susie@example.net")], (), ("susie@example.net",), 0),
        # a group owner, a manager, and a nested group are all untouchable
        ({}, [_user("noelle@example.net", GroupsPermissionLevel.Owner)], (), (), 0),
        ({}, [_user("berdly@example.net", GroupsPermissionLevel.Manager)], (), (), 0),
        ({}, [_user("lightners@example.net", type="GROUP")], (), (), 0),
        # an entry with unknown type is protected, not guessed at
        ({}, [_user("spamton@example.net", type=None)], (), (), 0),
        # a permission level Google reports but we do not model maps to Other,
        # never Member, so Gaster is protected rather than swept out
        (
            {},
            [_user("gaster@example.net", GroupsPermissionLevel("REDACTED"))],
            (),
            (),
            0,
        ),
        # present as a manager: not re-added as a member even though wanted as one
        (
            {"berdly@example.net": MEMBER_ID},
            [_user("berdly@example.net", GroupsPermissionLevel.Manager)],
            (),
            (),
            0,
        ),
        # Kris is wanted but absent, so gets added
        ({"kris@example.net": MEMBER_ID}, [], ("kris@example.net",), (), 0),
    ],
    ids=[
        "converged",
        "case-insensitive",
        "stranger-removed",
        "owner-protected",
        "manager-protected",
        "nested-group-protected",
        "unknown-type-protected",
        "unknown-permission-protected",
        "present-as-manager",
        "missing-added",
    ],
)
def test_plan_group_edges(desired, actual, expect_adds, expect_removes, expect_refused):
    plan = plan_group(GROUP, desired, actual, allow_mass_removal=False)
    assert tuple(a.address for a in plan.adds) == tuple(expect_adds)
    assert plan.removes == tuple(Email(r) for r in expect_removes)
    assert plan.refused_removes == expect_refused


def test_fuse_trips_on_mass_removal_and_spares_adds() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(100)]
    desired = {"ralsei@example.net": MEMBER_ID}
    plan = plan_group(GROUP, desired, actual, allow_mass_removal=False)
    assert plan.refused_removes == 100
    assert plan.removes == ()
    assert tuple(a.address for a in plan.adds) == ("ralsei@example.net",)
    assert plan.fuse_tripped


def test_fuse_floor_lets_a_small_group_shrink() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(4)]
    plan = plan_group(GROUP, {}, actual, allow_mass_removal=False)
    assert plan.removes and plan.refused_removes == 0


def test_allow_mass_removal_overrides_the_fuse() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(100)]
    plan = plan_group(GROUP, {}, actual, allow_mass_removal=True)
    assert len(plan.removes) == 100
    assert plan.refused_removes == 0
