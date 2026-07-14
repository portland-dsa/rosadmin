from __future__ import annotations

import logging

import httplib2
import pytest

from googleapiclient.errors import HttpError

from rosadmin.google_group import GoogleGroup
from rosadmin.group_sync import (
    DEFAULT_WRITE_RATE,
    DryRunGroupSync,
    GoogleGroupSync,
    SyncOutcome,
    _add_outcome,
    _remove_outcome,
    _skip_gate,
    _write_rate_from_env,
    group_sync_from_env,
)
from rosadmin.membership.source import Email


def _fake_http_error(status: str = "500", uri: str = "") -> HttpError:
    return HttpError(resp=httplib2.Response({"status": status}), content=b"{}", uri=uri)


def _sync_with_stub_services(monkeypatch, add_member=None, remove_member=None):
    """A `GoogleGroupSync` whose `GoogleGroup.add_member`/`remove_member` are
    stubbed, bypassing the real service-client build entirely."""

    async def _fake_services(self):
        return (object(), object(), object())

    monkeypatch.setattr(GoogleGroupSync, "_services", _fake_services)
    if add_member is not None:
        monkeypatch.setattr(GoogleGroup, "add_member", add_member)
    if remove_member is not None:
        monkeypatch.setattr(GoogleGroup, "remove_member", remove_member)
    return GoogleGroupSync(
        creds=object(),  # pyright: ignore[reportArgumentType]
        expect_example_emails=False,
    )


def test_group_sync_from_env_dry_run_selects_dry_run_sync():
    sync = group_sync_from_env({"ROSADMIN_GOOGLE_DRY_RUN": "1"})
    assert isinstance(sync, DryRunGroupSync)


def test_group_sync_from_env_real_path_without_subject_raises():
    with pytest.raises(RuntimeError, match="ROSADMIN_GOOGLE_SUBJECT"):
        group_sync_from_env({})


def test_group_sync_from_env_real_path_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_PATH", raising=False)
    with pytest.raises(RuntimeError, match="credentials"):
        group_sync_from_env({"ROSADMIN_GOOGLE_SUBJECT": "leader@example.org"})


def test_write_rate_defaults_when_unset():
    assert _write_rate_from_env({}) == DEFAULT_WRITE_RATE


def test_write_rate_parses_a_value():
    assert _write_rate_from_env({"ROSADMIN_GOOGLE_WRITE_RATE": "5"}) == 5.0


@pytest.mark.parametrize("bad", ["nope", "", "0", "-1"])
def test_write_rate_rejects_non_positive_or_non_numeric(bad):
    with pytest.raises(RuntimeError, match="ROSADMIN_GOOGLE_WRITE_RATE"):
        _write_rate_from_env({"ROSADMIN_GOOGLE_WRITE_RATE": bad})


def test_example_skip_logs_warning_by_default(caplog):
    with caplog.at_level(logging.WARNING, logger="rosadmin.group_sync"):
        outcome = _skip_gate(
            Email("group@example.org"),
            Email("member@example.com"),
            expect_example_emails=False,
        )
    assert outcome == SyncOutcome.SkippedExampleEmail
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_example_skip_logs_info_when_expected(caplog):
    with caplog.at_level(logging.INFO, logger="rosadmin.group_sync"):
        outcome = _skip_gate(
            Email("group@example.org"),
            Email("member@example.com"),
            expect_example_emails=True,
        )
    assert outcome == SyncOutcome.SkippedExampleEmail
    assert all(record.levelno != logging.WARNING for record in caplog.records)
    assert any(record.levelno == logging.INFO for record in caplog.records)


@pytest.mark.asyncio
async def test_http_error_from_add_maps_to_failed(monkeypatch, caplog):
    # A remove's request URI carries the member address as a path segment,
    # which is exactly what the failure log must not echo.
    member = "susie@dark.world"
    uri = f"https://admin.googleapis.com/admin/directory/v1/groups/group%40example.org/members/{member}"

    async def _raise(self, email):
        raise _fake_http_error(status="403", uri=uri)

    sync = _sync_with_stub_services(monkeypatch, add_member=_raise)
    with caplog.at_level(logging.ERROR, logger="rosadmin.group_sync"):
        outcome = await sync.add(Email("group@example.org"), Email(member))
    assert outcome == SyncOutcome.Failed
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert member not in caplog.text


@pytest.mark.asyncio
async def test_http_error_409_on_add_is_already_converged(monkeypatch, caplog):
    async def _raise(self, email):
        raise _fake_http_error(status="409")

    sync = _sync_with_stub_services(monkeypatch, add_member=_raise)
    with caplog.at_level(logging.WARNING, logger="rosadmin.group_sync"):
        outcome = await sync.add(Email("group@example.org"), Email("susie@dark.world"))
    assert outcome == SyncOutcome.AlreadyConverged
    assert "group@example.org" in caplog.text
    assert "susie@dark.world" not in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.asyncio
async def test_http_error_404_on_remove_is_already_converged(monkeypatch, caplog):
    member = "susie@dark.world"
    uri = f"https://admin.googleapis.com/admin/directory/v1/groups/group%40example.org/members/{member}"

    async def _raise(self, email):
        raise _fake_http_error(status="404", uri=uri)

    sync = _sync_with_stub_services(monkeypatch, remove_member=_raise)
    with caplog.at_level(logging.WARNING, logger="rosadmin.group_sync"):
        outcome = await sync.remove(Email("group@example.org"), Email(member))
    assert outcome == SyncOutcome.AlreadyConverged
    assert "group@example.org" in caplog.text
    assert member not in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.asyncio
async def test_http_error_404_on_add_still_fails(monkeypatch):
    """404 is the converged signal on a remove, never on an add - and with no
    `notFound` reason naming the address, it is not a verdict about one either."""

    async def _raise(self, email):
        raise _fake_http_error(status="404")

    sync = _sync_with_stub_services(monkeypatch, add_member=_raise)
    outcome = await sync.add(Email("group@example.org"), Email("member@example.org"))
    assert outcome == SyncOutcome.Failed


@pytest.mark.asyncio
async def test_http_error_409_on_remove_still_fails(monkeypatch):
    """409 is only the converged signal on an add; on a remove it's a real failure."""

    async def _raise(self, email):
        raise _fake_http_error(status="409")

    sync = _sync_with_stub_services(monkeypatch, remove_member=_raise)
    outcome = await sync.remove(Email("group@example.org"), Email("member@example.org"))
    assert outcome == SyncOutcome.Failed


@pytest.mark.parametrize(
    ("op", "status", "reasons", "expected"),
    [
        ("add", 409, set(), SyncOutcome.AlreadyConverged),
        ("add", 412, {"conditionNotMet"}, SyncOutcome.NoGoogleAccount),
        ("add", 404, {"notFound"}, SyncOutcome.AddressNotFound),
        ("add", 412, {"backendError"}, SyncOutcome.Failed),
        ("add", 403, {"quotaExceeded"}, SyncOutcome.Failed),
        ("add", 500, set(), SyncOutcome.Failed),
        ("remove", 404, set(), SyncOutcome.AlreadyConverged),
        ("remove", 500, set(), SyncOutcome.Failed),
    ],
)
def test_classifiers_read_googles_refusal(op, status, reasons, expected):
    """The whole status-and-reason table, in one place.

    A unit rather than a scenario because there is no asking Google for a 412 on
    demand: the classifiers are pure, and this is the only layer where every
    answer Google can give is reachable.
    """
    outcome = _add_outcome(status, reasons) if op == "add" else _remove_outcome(status)
    assert outcome == expected
