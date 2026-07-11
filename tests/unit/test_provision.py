"""The provisioning phase's pure pieces: the tripwire, the plan, and drift.

These never touch Postgres or Google - they are the riskiest logic (the
self-arming creation cap, the naming plan, the adopted-group divergence check)
isolated from the DB-bound sweep so they can be proven offline.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from rosadmin.db.directory import BodyLinkRow
from rosadmin.google_group import SECURE_SETTINGS, SECURITY_LABEL
from rosadmin.group_naming import GroupKind, GroupNameTooLong
from rosadmin.group_sync import ProvisionedGroup
from rosadmin.membership.source import Email
from rosadmin.reconcile import (
    ProvisionConfig,
    _diverged,
    _plan_body,
    _plan_pending,
    _tripwire_refuses,
)

CONFIG = ProvisionConfig(
    domain="example.net", main_group_name="Everyone", mass_creation_tripwire=5
)
MAIN = Email("everyone@example.net")


def _body(name: str, body_type: str) -> BodyLinkRow:
    return BodyLinkRow(
        id=uuid4(),
        name=name,
        body_type=body_type,
        leader_google_group_email=None,
        member_google_group_email=None,
    )


def _linked_body(name: str, body_type: str, leader: str, member: str) -> BodyLinkRow:
    """A body already linked on both columns - the shape `_plan_pending` skips."""
    return BodyLinkRow(
        id=uuid4(),
        name=name,
        body_type=body_type,
        leader_google_group_email=leader,
        member_google_group_email=member,
    )


def _secure_group() -> ProvisionedGroup:
    """A group whose settings and labels exactly match the secure defaults."""
    return ProvisionedGroup(
        settings={k: str(v) for k, v in SECURE_SETTINGS.items()},
        labels=dict(SECURITY_LABEL.get("labels", {})),
    )


@pytest.mark.parametrize(
    ("bootstrapped", "creations", "refuses"),
    [
        # never bootstrapped: the seeding run mints freely, cap ignored
        (False, 999, False),
        (False, 0, False),
        # armed and over the cap: refuse
        (True, 6, True),
        # armed and at or under the cap: allow
        (True, 5, False),
        (True, 0, False),
    ],
    ids=["seed-huge", "seed-none", "armed-over", "armed-at-cap", "armed-under"],
)
def test_tripwire_refuses(bootstrapped, creations, refuses):
    assert (
        _tripwire_refuses(bootstrapped, creations, CONFIG.mass_creation_tripwire)
        is refuses
    )


def test_plan_pending_names_each_bodys_pair_plus_the_main_group():
    bodies = [_body("Cyber World", "Working Group"), _body("Card Castle", "Committee")]
    pending, name_failures = _plan_pending(bodies, CONFIG, MAIN)

    assert name_failures == 0
    # The main group, then a leaders and an editors group per body.
    assert len(pending) == 1 + 2 * len(bodies)
    main = pending[0]
    assert main.email == MAIN
    assert main.name == "Everyone"
    assert main.body_id is None and main.kind is None

    by_email = {g.email: g for g in pending}
    assert set(by_email) == {
        MAIN,
        "cyber-world-working-group-leaders@example.net",
        "cyber-world-working-group-editors@example.net",
        "card-castle-committee-leaders@example.net",
        "card-castle-committee-editors@example.net",
    }
    leaders = by_email[Email("cyber-world-working-group-leaders@example.net")]
    assert leaders.name == "Cyber World Working Group Leaders"
    assert leaders.kind is GroupKind.Leaders
    assert leaders.body_id == bodies[0].id
    editors = by_email[Email("card-castle-committee-editors@example.net")]
    assert editors.kind is GroupKind.Editors


def test_plan_pending_skips_a_body_linked_on_both_columns():
    # The stored addresses deliberately differ from what today's naming would
    # compute - a renamed or hand-linked body. It must still be skipped whole.
    linked = _linked_body(
        "Steering",
        "Committee",
        "steering-leaders@example.net",
        "steering-members@example.net",
    )
    unlinked = _body("Cyber World", "Working Group")
    pending, name_failures = _plan_pending([linked, unlinked], CONFIG, MAIN)

    assert name_failures == 0
    assert not any(g.body_id == linked.id for g in pending)
    assert {g.email for g in pending} == {
        MAIN,
        "cyber-world-working-group-leaders@example.net",
        "cyber-world-working-group-editors@example.net",
    }


def test_plan_pending_counts_an_unnameable_body_as_a_failure():
    # A name of many single-letter words overflows the email cap even fully
    # initialed, so it cannot be named within Google's limits.
    huge = _body(" ".join(["x"] * 70), "Committee")
    nameable = _body("Cyber World", "Working Group")
    pending, name_failures = _plan_pending([huge, nameable], CONFIG, MAIN)

    assert name_failures == 1
    # The un-nameable body drops out; the main group and the nameable body stay.
    assert {g.email for g in pending} == {
        MAIN,
        "cyber-world-working-group-leaders@example.net",
        "cyber-world-working-group-editors@example.net",
    }


def test_plan_body_raises_when_a_name_overflows_the_cap():
    with pytest.raises(GroupNameTooLong):
        _plan_body(_body(" ".join(["x"] * 70), "Committee"), CONFIG)


def test_diverged_is_false_for_a_group_matching_the_secure_defaults():
    assert _diverged(_secure_group()) is False


def test_diverged_is_true_when_a_setting_is_slack():
    seen = _secure_group()
    seen.settings["whoCanJoin"] = "ALL_IN_DOMAIN_CAN_JOIN"
    assert _diverged(seen) is True


def test_diverged_is_true_when_the_security_labels_are_missing():
    seen = ProvisionedGroup(
        settings={k: str(v) for k, v in SECURE_SETTINGS.items()}, labels={}
    )
    assert _diverged(seen) is True
