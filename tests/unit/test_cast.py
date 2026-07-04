from __future__ import annotations

from rosadmin.mock_st.cast import Identity, identity_for


def test_full_name_prefers_the_chosen_name_over_first():
    # The chosen-name rule: alternate_name (a preferred/chosen name) wins over a
    # possibly-legal-or-dead first_name; without it the first name stands.
    assert Identity("Deadname", "Surname", "Chosen").full_name == "Chosen Surname"
    assert Identity("Kris", "Dreemurr").full_name == "Kris Dreemurr"


def test_identity_for_returns_the_cast_then_falls_back():
    assert identity_for("kris@example.com").last_name == "Dreemurr"
    assert identity_for("nobody@example.com").first_name == "Nobody"
