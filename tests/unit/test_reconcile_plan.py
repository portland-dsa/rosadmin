"""The reconcile core's pure edges: the plan function, and the ceiling that will
not let a run learn a mass refusal quietly."""

from __future__ import annotations

from collections import Counter
from uuid import uuid4

import pytest

from rosadmin.google_group import GroupMemberEntry, GroupsPermissionLevel
from rosadmin.group_sync import SyncOutcome
from rosadmin.membership.source import Email
from rosadmin.reconcile import (
    REFUSAL_FUSE_CEILING,
    Presence,
    RefusalReport,
    SweepReport,
    _abandoned,
    plan_group,
)


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

#: Most edges are about role, type, and case, and nothing Google has refused is
#: in play for them - naming the empty set once keeps the table's refusal column
#: readable.
NONE_REFUSED: set[str] = set()


@pytest.mark.parametrize(
    (
        "desired",
        "actual",
        "unmirrorable",
        "expect_adds",
        "expect_removes",
        "expect_excluded",
        "expect_refused",
    ),
    [
        # converged: Ralsei is wanted and already present, nothing to do
        (
            {"ralsei@example.net": MEMBER_ID},
            [_user("ralsei@example.net")],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        # case-insensitive match: the same Ralsei, cased differently, is no change
        (
            {"ralsei@example.net": MEMBER_ID},
            [_user("Ralsei@Example.NET")],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        # Susie is wanted by no one and gets removed
        (
            {},
            [_user("susie@example.net")],
            NONE_REFUSED,
            (),
            ("susie@example.net",),
            (),
            0,
        ),
        # a group owner, a manager, and a nested group are all untouchable
        (
            {},
            [_user("noelle@example.net", GroupsPermissionLevel.Owner)],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        (
            {},
            [_user("berdly@example.net", GroupsPermissionLevel.Manager)],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        (
            {},
            [_user("lightners@example.net", type="GROUP")],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        # an entry with unknown type is protected, not guessed at
        ({}, [_user("spamton@example.net", type=None)], NONE_REFUSED, (), (), (), 0),
        # a permission level Google reports but we do not model maps to Other,
        # never Member, so Gaster is protected rather than swept out
        (
            {},
            [_user("gaster@example.net", GroupsPermissionLevel("REDACTED"))],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        # present as a manager: not re-added as a member even though wanted as one
        (
            {"berdly@example.net": MEMBER_ID},
            [_user("berdly@example.net", GroupsPermissionLevel.Manager)],
            NONE_REFUSED,
            (),
            (),
            (),
            0,
        ),
        # Kris is wanted but absent, so gets added
        (
            {"kris@example.net": MEMBER_ID},
            [],
            NONE_REFUSED,
            ("kris@example.net",),
            (),
            (),
            0,
        ),
        # Google has refused both Spamtons. The absent one is withheld rather than
        # offered again; the one already in the group is left exactly where it is,
        # because a refused address stays desired and so is never a stranger. Were
        # it dropped from desired instead, this row's removes would hold it.
        (
            {"spamton@example.net": MEMBER_ID, "spamton-g@example.net": MEMBER_ID},
            [_user("spamton-g@example.net")],
            {"spamton@example.net", "spamton-g@example.net"},
            (),
            (),
            ("spamton@example.net",),
            0,
        ),
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
        "refused-withheld-never-removed",
    ],
)
def test_plan_group_edges(
    desired,
    actual,
    unmirrorable,
    expect_adds,
    expect_removes,
    expect_excluded,
    expect_refused,
):
    plan = plan_group(
        GROUP, desired, actual, unmirrorable=unmirrorable, allow_mass_removal=False
    )
    assert tuple(a.address for a in plan.adds) == tuple(expect_adds)
    assert plan.removes == tuple(Email(r) for r in expect_removes)
    assert tuple(e.address for e in plan.excluded) == tuple(expect_excluded)
    assert plan.refused_removes == expect_refused


def test_fuse_trips_on_mass_removal_and_spares_adds() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(100)]
    desired = {"ralsei@example.net": MEMBER_ID}
    plan = plan_group(
        GROUP, desired, actual, unmirrorable=NONE_REFUSED, allow_mass_removal=False
    )
    assert plan.refused_removes == 100
    assert plan.removes == ()
    assert tuple(a.address for a in plan.adds) == ("ralsei@example.net",)
    assert plan.fuse_tripped


def test_fuse_floor_lets_a_small_group_shrink() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(4)]
    plan = plan_group(
        GROUP, {}, actual, unmirrorable=NONE_REFUSED, allow_mass_removal=False
    )
    assert plan.removes and plan.refused_removes == 0


def test_allow_mass_removal_overrides_the_fuse() -> None:
    actual = [_user(f"townie{i}@example.net") for i in range(100)]
    plan = plan_group(
        GROUP, {}, actual, unmirrorable=NONE_REFUSED, allow_mass_removal=True
    )
    assert len(plan.removes) == 100
    assert plan.refused_removes == 0


def _report(refusals: RefusalReport) -> SweepReport:
    return SweepReport(pull=None, groups=(), lister_available=True, refusals=refusals)


def test_a_trickle_of_refusals_is_recorded_and_the_run_stays_green() -> None:
    received = REFUSAL_FUSE_CEILING
    report = _report(RefusalReport(received=received, recorded=received, refused=0))
    assert not report.has_failures


def test_a_refusal_the_database_would_not_take_fails_the_run() -> None:
    """Silence about a refusal is the one thing that cannot be tolerated: an
    unrecorded address is offered to Google again in four hours, and forever."""
    report = _report(RefusalReport(received=3, recorded=2, refused=0))
    assert report.has_failures


def test_a_mass_refusal_is_refused_wholesale_and_fails_the_run() -> None:
    """Something outside the roster has changed and Google refuses everyone. The
    fuse records none of it - suppressing the roster for a season would be the
    real damage - and the run is red for as long as it keeps happening."""
    received = REFUSAL_FUSE_CEILING + 1
    report = _report(RefusalReport(received=received, recorded=0, refused=received))
    assert report.has_failures


@pytest.mark.parametrize("presence", [Presence.Gone, Presence.Unknown])
def test_abandoning_a_group_counts_everything_it_left_undone(presence):
    """Susie's group is gone by the time the sweep offers it a member. Whatever
    the plan still wanted is a failure - so the run goes red - and no arithmetic
    can talk it back down to zero."""
    plan = plan_group(
        GROUP,
        {f"townie{i}@example.net": MEMBER_ID for i in range(10)},
        [_user("stranger@example.net")],
        unmirrorable=NONE_REFUSED,
        allow_mass_removal=False,
    )
    # Two adds got through before the group vanished; one was refused outright.
    tally = Counter(
        {
            SyncOutcome.Applied: 2,
            SyncOutcome.NoGoogleAccount: 1,
        }
    )
    outcome = _abandoned(plan, tally, presence)
    assert outcome.planned_adds == 10 and outcome.planned_removes == 1
    assert outcome.applied == 2
    # Ten adds, of which three settled (two applied, one refused outright). The
    # rest is what the group was left owing: the add whose 404 raised the
    # question, the six never attempted behind it, and the remove the sweep
    # declined to make against a group that may not be there.
    assert outcome.failed == 1 + 6 + 1
    assert outcome.failed > 0
