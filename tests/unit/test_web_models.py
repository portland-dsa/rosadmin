from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from rosadmin.web.models import SearchHit, SearchMiss, SearchResponse

_ADAPTER: TypeAdapter[SearchHit | SearchMiss] = TypeAdapter(SearchResponse)


def test_search_response_discriminates_on_status():
    hit = _ADAPTER.validate_python(
        {
            "status": "good_standing",
            "member": {
                "id": str(uuid4()),
                "full_name": "Kris",
                "email": "k@example.com",
            },
        }
    )
    assert isinstance(hit, SearchHit)
    miss = _ADAPTER.validate_python({"status": "dues_expired"})
    assert isinstance(miss, SearchMiss)


def test_good_standing_requires_the_member_object():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"status": "good_standing"})


def test_misses_reject_a_member_object():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(
            {
                "status": "not_found",
                "member": {
                    "id": str(uuid4()),
                    "full_name": "X",
                    "email": "x@example.com",
                },
            }
        )
