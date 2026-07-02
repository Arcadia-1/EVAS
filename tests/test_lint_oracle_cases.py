"""Oracle-backed lint case fixtures.

The fixtures in tests/fixtures/lint_oracle_cases are hand-distilled from
Cadence/Spectre behavior, but they intentionally avoid committing raw simulator
logs or generated certification reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evas.compiler.linter import LINT_RULE_SPECS, lint_file

ORACLE_DIR = Path(__file__).parent / "fixtures" / "lint_oracle_cases"
MANIFEST = ORACLE_DIR / "manifest.json"


def _load_cases() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
def test_lint_oracle_case_expected_codes(case):
    va_file = ORACLE_DIR / case["file"]
    diagnostics = lint_file(va_file)
    codes = {diag.code for diag in diagnostics}

    assert va_file.exists(), f"missing oracle case file: {va_file}"
    assert set(case["expected_codes"]) <= codes
    assert not (set(case.get("forbidden_codes", [])) & codes)
    assert all(code in LINT_RULE_SPECS for code in case["expected_codes"])

    for diagnostic in diagnostics:
        if diagnostic.code in case["expected_codes"]:
            assert diagnostic.line is not None
            assert diagnostic.column is not None


def test_lint_oracle_manifest_is_public_and_minimal():
    cases = _load_cases()
    assert cases
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert set(case) == {
            "id",
            "file",
            "description",
            "expected_codes",
            "forbidden_codes",
            "oracle_note",
        }
        assert "raw simulator output is intentionally not committed" in case["oracle_note"]
        assert not Path(case["file"]).is_absolute()
        assert (ORACLE_DIR / case["file"]).exists()
