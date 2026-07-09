from __future__ import annotations

from rosadmin.db.audit import audit_key_from_env


def test_audit_key_matches_across_file_and_env(tmp_path):
    # A credential file written with a trailing newline must yield the same key as
    # the same secret handed in via the env var - otherwise one actor's audit
    # history forks across two HMACs.
    (tmp_path / "audit-hmac-key").write_text("s3cret-key\n", encoding="utf-8")

    from_file = audit_key_from_env({"CREDENTIALS_DIRECTORY": str(tmp_path)})
    from_env = audit_key_from_env({"ROSADMIN_AUDIT_HMAC_KEY": "s3cret-key"})

    assert from_file == from_env == b"s3cret-key"
