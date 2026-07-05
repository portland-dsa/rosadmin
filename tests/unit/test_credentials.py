from __future__ import annotations

from rosadmin.credentials import read_credential


def test_credential_file_wins_over_env(tmp_path):
    (tmp_path / "secret").write_text("from-file\n", encoding="utf-8")
    env = {"CREDENTIALS_DIRECTORY": str(tmp_path), "SECRET_ENV": "from-env"}
    assert read_credential(env, "secret", "SECRET_ENV") == "from-file"


def test_credential_falls_back_to_env_when_no_file(tmp_path):
    # The credential directory is set but holds no such file: the env var answers.
    env = {"CREDENTIALS_DIRECTORY": str(tmp_path), "SECRET_ENV": "from-env"}
    assert read_credential(env, "secret", "SECRET_ENV") == "from-env"


def test_empty_credential_file_falls_through_to_env(tmp_path):
    # An empty (or whitespace-only) file is treated as absent, not as an empty
    # secret, so a present env var still answers rather than being shadowed.
    (tmp_path / "secret").write_text("   \n", encoding="utf-8")
    env = {"CREDENTIALS_DIRECTORY": str(tmp_path), "SECRET_ENV": "from-env"}
    assert read_credential(env, "secret", "SECRET_ENV") == "from-env"


def test_absent_from_both_sources_is_none():
    assert read_credential({}, "secret", "SECRET_ENV") is None


def test_value_is_whitespace_stripped(tmp_path):
    # Both delivery paths strip, so a credential file's trailing newline yields
    # the same value as the same secret set inline.
    (tmp_path / "secret").write_text("padded\n", encoding="utf-8")
    env = {"CREDENTIALS_DIRECTORY": str(tmp_path)}
    assert read_credential(env, "secret", "SECRET_ENV") == "padded"
