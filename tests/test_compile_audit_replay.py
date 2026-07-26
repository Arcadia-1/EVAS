import json
import textwrap
from pathlib import Path

from evas.netlist.compile_audit_replay import (
    iter_compile_audit_cases,
    iter_compile_transition_cases,
    replay_compile_audit_scores,
)
from evas.simulator.engine import Simulator


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _score_row(
    root: Path,
    *,
    cell_id: str,
    source_outcome: str,
    outcome: str,
    case_id: str,
    scs_text: str | None,
    failure_kind: str | None = None,
    spectre_ok: bool | None = None,
) -> dict:
    sidecar = root / "cells" / cell_id / "input" / "result.json"
    scs = sidecar.parent / "cases" / case_id / f"{cell_id}_{case_id}__tb_score.scs"
    if scs_text is not None:
        _write(scs, scs_text)
    _write(sidecar, "{}")
    return {
        "cell_id": cell_id,
        "source_outcome": source_outcome,
        "outcome": outcome,
        "sidecar_result_path": str(sidecar),
        "cases": [
            {
                "case_id": case_id,
                "failure_kind": failure_kind,
                "spectre": {"ok": spectre_ok},
            }
        ],
    }


def _score_file(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        json.dumps({"schema_version": "test", "rows": rows}),
        encoding="utf-8",
    )
    return path


def test_iter_compile_audit_cases_uses_case_level_spectre_compile_labels(tmp_path):
    rows = [
        _score_row(
            tmp_path,
            cell_id="behavior-to-compile",
            source_outcome="behavior_failure",
            outcome="compile_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            failure_kind="compile",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="stale-runtime-kind-is-row-compile-reject",
            source_outcome="passed",
            outcome="compile_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            failure_kind="runtime",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="stale-behavior-kind-is-row-compile-reject",
            source_outcome="passed",
            outcome="compile_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            failure_kind="behavior",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="compile-to-behavior",
            source_outcome="compile_failure",
            outcome="behavior_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            failure_kind="behavior",
            spectre_ok=True,
        ),
        _score_row(
            tmp_path,
            cell_id="unchanged-accept",
            source_outcome="passed",
            outcome="passed",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            spectre_ok=True,
        ),
        _score_row(
            tmp_path,
            cell_id="infrastructure-is-unknown",
            source_outcome="passed",
            outcome="infrastructure_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            failure_kind="infrastructure",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="false-run-status-is-not-by-itself-compile-reject",
            source_outcome="passed",
            outcome="behavior_failure",
            case_id="score",
            scs_text="simulator lang=spectre\n",
            spectre_ok=False,
        ),
    ]
    score = _score_file(tmp_path / "score.json", rows)

    all_cases = list(iter_compile_audit_cases([score]))
    transitions = list(iter_compile_transition_cases([score]))

    assert [(case.cell_id, case.expected_ok) for case in all_cases] == [
        ("behavior-to-compile", False),
        ("stale-runtime-kind-is-row-compile-reject", False),
        ("stale-behavior-kind-is-row-compile-reject", False),
        ("compile-to-behavior", True),
        ("unchanged-accept", True),
        ("false-run-status-is-not-by-itself-compile-reject", True),
    ]
    assert [(case.cell_id, case.expected_ok) for case in transitions] == [
        ("behavior-to-compile", False),
        ("stale-runtime-kind-is-row-compile-reject", False),
        ("stale-behavior-kind-is-row-compile-reject", False),
        ("compile-to-behavior", True),
    ]


def test_replay_compile_audit_scores_reports_mismatches_without_running_simulation(
    tmp_path,
    monkeypatch,
):
    rows = [
        _score_row(
            tmp_path,
            cell_id="accepts",
            source_outcome="compile_failure",
            outcome="passed",
            case_id="score",
            scs_text=(
                "simulator lang=spectre\n"
                "global 0\n"
                "V1 (out 0) vsource dc=1\n"
                "save out\n"
            ),
            spectre_ok=True,
        ),
        _score_row(
            tmp_path,
            cell_id="too-wide",
            source_outcome="passed",
            outcome="compile_failure",
            case_id="score",
            scs_text=(
                "simulator lang=spectre\n"
                "global 0\n"
                "V1 (out 0) vsource dc=1\n"
                "save out\n"
            ),
            failure_kind="compile",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="known-reject",
            source_outcome="passed",
            outcome="compile_failure",
            case_id="score",
            scs_text="simulator lang=spectre\nbogus statement\n",
            failure_kind="compile",
            spectre_ok=False,
        ),
        _score_row(
            tmp_path,
            cell_id="unchanged-accept",
            source_outcome="passed",
            outcome="passed",
            case_id="score",
            scs_text=(
                "simulator lang=spectre\n"
                "global 0\n"
                "V1 (out 0) vsource dc=1\n"
                "save out\n"
            ),
            spectre_ok=True,
        ),
    ]
    score = _score_file(tmp_path / "score.json", rows)

    def fail_run(self, *args, **kwargs):
        raise AssertionError("compile-audit replay must not run simulation")

    monkeypatch.setattr(Simulator, "run", fail_run)

    report = replay_compile_audit_scores([score], spectre_strict=True)

    assert report["summary"] == {
        "total": 4,
        "passed": 3,
        "failed": 1,
        "expected_accept": 2,
        "expected_reject": 2,
        "skipped_cases": 0,
        "input_errors": 0,
        "compile_exceptions": 0,
    }
    assert [
        (result["cell_id"], result["matched"]) for result in report["results"]
    ] == [
        ("accepts", True),
        ("too-wide", False),
        ("known-reject", True),
        ("unchanged-accept", True),
    ]


def test_replay_compile_audit_scores_reports_missing_case_artifact(tmp_path):
    score = _score_file(
        tmp_path / "score.json",
        [
            _score_row(
                tmp_path,
                cell_id="missing-scs",
                source_outcome="passed",
                outcome="passed",
                case_id="score",
                scs_text=None,
                spectre_ok=True,
            )
        ],
    )

    report = replay_compile_audit_scores([score])

    assert report["summary"]["input_errors"] == 1
    assert report["summary"]["failed"] == 1
    assert report["results"][0]["stage"] == "replay_input"
    assert "no .scs artifact" in report["results"][0]["diagnostics"][0]["message"]
