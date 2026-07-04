"""Regenerate the committed OpenAPI contract, openapi.json.

Run after any change to the contract models or routes, and commit the result;
tests/unit/test_openapi_contract.py fails if the committed file drifts from the code.

    uv run python scripts/dump_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from rosadmin.service import contract_schema

_ARTIFACT = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    _ARTIFACT.write_text(
        json.dumps(contract_schema(), indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {_ARTIFACT}")


if __name__ == "__main__":
    main()
