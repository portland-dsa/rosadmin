from __future__ import annotations

from rosadmin.db.directory import full_name


def test_full_name_prefers_alternate_over_first():
    assert full_name("Kris", "Dreemurr", "Frisk") == "Frisk Dreemurr"


def test_full_name_falls_back_to_first_when_no_alternate():
    assert full_name("Kris", "Dreemurr", None) == "Kris Dreemurr"


def test_full_name_drops_a_missing_last_name():
    assert full_name("Kris", None, None) == "Kris"
