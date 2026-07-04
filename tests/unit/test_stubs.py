from __future__ import annotations

from rosadmin_devtools.stubs import _ROSTER, _STATUS_BY_PERSONA


def test_status_mapping_is_total_over_the_roster():
    # A roster persona missing from the mapping would be served as not_found instead
    # of failing loudly; pin totality here.
    assert {p.persona for p in _ROSTER} <= set(_STATUS_BY_PERSONA)
