import pytest

from rosadmin.group_naming import (
    GoogleGroupEmail,
    GoogleGroupName,
    GroupKind,
    GroupNameTooLong,
)

DOMAIN = "example.com"


@pytest.mark.parametrize(
    "name, body_type, kind, expected",
    [
        (
            "Fountain",
            "Committee",
            GroupKind.Leaders,
            "fountain-committee-leaders@example.com",
        ),
        (
            "Fountain",
            "Committee",
            GroupKind.Editors,
            "fountain-committee-editors@example.com",
        ),
        (
            "Hopes & Dreams",
            "Working Group",
            GroupKind.Editors,
            "hopes-dreams-working-group-editors@example.com",
        ),
        (
            "Queen's Mansion",
            "Committee",
            GroupKind.Editors,
            "queens-mansion-committee-editors@example.com",
        ),
    ],
)
def test_email_full_form(name, body_type, kind, expected):
    assert str(GoogleGroupEmail(name, body_type, kind, DOMAIN)) == expected


def test_email_shortens_type_then_name_when_over_cap():
    # 50 chars: the full form overflows (50 + len("-working-group-leaders") = 72),
    # so the type initials to "wg" and 50 + len("-wg-leaders") = 61 fits.
    long_name = "a" * 50
    email = GoogleGroupEmail(long_name, "Working Group", GroupKind.Leaders, DOMAIN)
    assert email.local == f"{'a' * 50}-wg-leaders"  # type initialed, still <= 64
    # 70 chars: even type-initialed (70 + 11 = 81) overflows, so the name initials too.
    longer = "b" * 70
    email2 = GoogleGroupEmail(longer, "Working Group", GroupKind.Leaders, DOMAIN)
    assert email2.local == "b-wg-leaders"  # name initialed too


def test_email_raises_when_even_fully_initialed_overflows():
    with pytest.raises(GroupNameTooLong):
        # A name whose initials alone blow the cap: 70 space-separated letters.
        GoogleGroupEmail(" ".join("x" * 70), "Working Group", GroupKind.Leaders, DOMAIN)


@pytest.mark.parametrize(
    "name, body_type, kind, expected",
    [
        ("Fountain", "Committee", GroupKind.Leaders, "Fountain Committee Leaders"),
        ("fountain", "Committee", GroupKind.Editors, "Fountain Committee Editors"),
        (
            "Hopes & Dreams",
            "Working Group",
            GroupKind.Editors,
            "Hopes & Dreams Working Group Editors",
        ),
        ("TV World", "Branch", GroupKind.Leaders, "TV World Branch Leaders"),
    ],
)
def test_label_full_form(name, body_type, kind, expected):
    assert str(GoogleGroupName(name, body_type, kind)) == expected


def test_label_shortens_type_when_over_cap():
    # 55 chars: the full form overflows (55 + len(" Working Group Leaders") = 77),
    # so the body type initials to "WG" and 55 + len(" WG Leaders") = 66 fits - the
    # middle display tier, without collapsing the whole name to an acronym.
    label = GoogleGroupName("A" * 55, "Working Group", GroupKind.Leaders)
    assert label.label == f"{'A' * 55} WG Leaders"


def test_label_collapses_to_acronym_when_over_cap():
    label = GoogleGroupName("Cyber World", "Working Group", GroupKind.Leaders)
    assert label.label == "Cyber World Working Group Leaders"  # fits; no shortening
    long_name = "Cyber World " + "Word " * 20
    collapsed = GoogleGroupName(long_name, "Working Group", GroupKind.Leaders)
    assert collapsed.label.endswith(" Leaders")
    assert " " not in collapsed.label[: -len(" Leaders")]  # a single no-space acronym
