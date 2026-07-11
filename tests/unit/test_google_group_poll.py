from __future__ import annotations

import json
from typing import Any

import httplib2
import pytest

from googleapiclient.errors import HttpError

from rosadmin.google_group import PropagationTimeout, _poll_until, _retry_transient


def _http_error(status: str, reason: str | None = None) -> HttpError:
    # With a reason, the body mimics the Admin SDK's error envelope, which is
    # where `HttpError.error_details` reads the reason from.
    # The "message" key matters: the client library only populates
    # `error_details` when the envelope carries one.
    content = (
        json.dumps(
            {
                "error": {
                    "errors": [{"reason": reason, "message": "m"}],
                    "code": int(status),
                    "message": "m",
                }
            }
        )
        if reason is not None
        else "{}"
    ).encode()
    return HttpError(resp=httplib2.Response({"status": status}), content=content)


def test_predicate_true_on_first_call_returns_immediately():
    calls = 0

    def visible() -> bool:
        nonlocal calls
        calls += 1
        return True

    _poll_until(visible)
    assert calls == 1


def test_predicate_false_then_true_retries_until_success():
    calls = 0

    def visible() -> bool:
        nonlocal calls
        calls += 1
        return calls > 3

    _poll_until(visible, interval=0)
    assert calls == 4


def test_predicate_never_true_raises_propagation_timeout():
    with pytest.raises(PropagationTimeout):
        _poll_until(lambda: False, interval=0, ceiling=0.05)


def test_predicate_error_propagates_unwrapped():
    def visible() -> bool:
        raise ValueError("real failure")

    with pytest.raises(ValueError, match="real failure"):
        _poll_until(visible, interval=0)


def test_transient_http_error_retries_until_success():
    calls = 0

    def visible() -> bool:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise _http_error("503")
        return True

    _poll_until(visible, interval=0)
    assert calls == 3


def test_non_transient_http_error_propagates_immediately():
    calls = 0

    def visible() -> bool:
        nonlocal calls
        calls += 1
        raise _http_error("403", reason="forbidden")

    with pytest.raises(HttpError):
        _poll_until(visible, interval=0)
    assert calls == 1


def test_a_rate_limited_403_is_ridden_out_like_a_429():
    # The Admin SDK reports most Directory rate limiting as a 403 with a
    # rate-limit reason, not a 429; only the reason separates it from a real
    # permission failure, which the test above pins as immediately fatal.
    calls = 0

    def visible() -> bool:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise _http_error("403", reason="userRateLimitExceeded")
        return True

    _poll_until(visible, interval=0)
    assert calls == 3


def test_persistent_transient_http_error_raises_propagation_timeout():
    def visible() -> bool:
        raise _http_error("503")

    with pytest.raises(PropagationTimeout):
        _poll_until(visible, interval=0, ceiling=0.05)


class _FlakyCall:
    """A callable failing with the given errors before finally succeeding."""

    def __init__(self, errors: list[Exception], result: Any) -> None:
        self._errors = list(errors)
        self._result = result
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._result


def test_retry_transient_waits_out_a_429_then_returns() -> None:
    call = _FlakyCall([_http_error("429")], result="ok")
    assert _retry_transient(call) == "ok"
    assert call.calls == 2


def test_retry_transient_propagates_a_412_immediately() -> None:
    # A 412 conditionNotMet is not a transient blip that clears on backoff: when
    # a group is in the state that draws it, every insert fails and retrying only
    # stalls each one through the whole backoff. It surfaces at once instead.
    call = _FlakyCall([_http_error("412")], result="never")
    with pytest.raises(HttpError):
        _retry_transient(call)
    assert call.calls == 1


def test_retry_transient_propagates_a_404_immediately() -> None:
    call = _FlakyCall([_http_error("404")], result="never")
    with pytest.raises(HttpError):
        _retry_transient(call)
    assert call.calls == 1
