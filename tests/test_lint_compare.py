import json
import subprocess
import sys
from pathlib import Path

from evas.compiler.lint_compare import (
    compare_lint_cases,
    extract_cadence_cases,
    extract_evas_cases,
)


def test_lint_compare_classifies_missing_and_false_compat():
    cadence = {
        "cases": [
            {
                "name": "array_range_variable",
                "status": "failure",
                "code_counts": {"VACOMP-2435": 2, "VACOMP-2446": 2},
            },
            {
                "name": "clean_case",
                "status": "success",
                "code_counts": {"VACOMP-2435": 2},
            },
        ]
    }
    evas = {
        "rows": [
            {
                "case_id": "array_range_variable",
                "status": "PASS",
                "diagnostics": [],
            },
            {
                "case_id": "clean_case",
                "status": "FAIL_COMPAT",
                "diagnostics": [
                    {
                        "code": "EVAS-COMP-E2157",
                        "severity": "compat-error",
                        "spectre_ids": ["VACOMP-2157"],
                    }
                ],
            },
        ]
    }

    comparisons = compare_lint_cases(
        extract_cadence_cases(cadence),
        extract_evas_cases(evas),
    )
    by_id = {item.case_id: item for item in comparisons}

    assert by_id["array_range_variable"].category == "cadence_failure_without_evas_compat"
    assert by_id["array_range_variable"].missing_cadence_codes == ["VACOMP-2446"]
    assert by_id["clean_case"].category == "evas_false_compat_error"
    assert by_id["clean_case"].extra_evas_spectre_ids == ["VACOMP-2157"]


def test_lint_compare_uses_rule_registry_spectre_ids():
    cadence = {
        "cases": [
            {
                "name": "event_body",
                "status": "failure",
                "code_counts": {"VACOMP-2157": 1},
            }
        ]
    }
    evas = {
        "rows": [
            {
                "case_id": "event_body",
                "status": "FAIL_COMPAT",
                "diagnostics": [
                    {
                        "code": "EVAS-COMP-E2157",
                        "severity": "compat-error",
                    }
                ],
            }
        ]
    }

    comparisons = compare_lint_cases(
        extract_cadence_cases(cadence),
        extract_evas_cases(evas),
    )

    assert comparisons[0].category == "aligned"
    assert comparisons[0].matched_spectre_ids == ["VACOMP-2157"]


def test_lint_compare_cli_writes_json(tmp_path):
    cadence_path = tmp_path / "cadence.json"
    evas_path = tmp_path / "evas.json"
    cadence_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "transition_case",
                        "status": "success",
                        "code_counts": {"AHDLLINT-5007": 1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    evas_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "case_id": "transition_case",
                        "status": "WARN",
                        "diagnostics": [
                            {
                                "code": "EVAS-AHDL-W5007",
                                "severity": "static-warning",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_ahdl_lint.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--cadence-summary",
            str(cadence_path),
            "--evas-report",
            str(evas_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"] == {
        "cases_total": 1,
        "categories": {"aligned": 1},
        "evas_specific_code_total": 0,
        "extra_evas_spectre_id_total": 0,
        "missing_cadence_code_total": 0,
    }
