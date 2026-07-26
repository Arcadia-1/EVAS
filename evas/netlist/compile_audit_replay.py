"""Offline compile-acceptance replay for Spectre audit score files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from evas.netlist.runner import compile_spectre_netlist

SCHEMA_VERSION = "evas-compile-audit-replay-v3"


@dataclass(frozen=True)
class CompileAuditCase:
    score_path: Path
    cell_id: str
    case_id: str
    scs_path: Path | None
    source_outcome: str
    spectre_outcome: str
    failure_kind: str
    source_ok: bool | None
    expected_ok: bool
    discovery_error: str | None = None


def _load_score_rows(score_path: Path) -> list[dict]:
    data = json.loads(score_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{score_path} does not contain a JSON object")
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError(f"{score_path} does not contain a list-valued 'rows'")
    return rows


def _source_compile_ok(source_outcome: str) -> bool | None:
    if source_outcome == "compile_failure":
        return False
    if source_outcome in {"passed", "behavior_failure", "runtime_failure"}:
        return True
    return None


def _spectre_compile_ok(row: dict, case: dict) -> bool | None:
    """Classify target compile acceptance without stale case labels winning.

    Row-level target compile and infrastructure outcomes are authoritative.
    For rows whose target reached a later stage, case-level compile labels still
    capture per-case rejection evidence, while false run status alone does not.
    """
    outcome = str(row.get("outcome") or "").lower()
    if outcome == "compile_failure":
        return False
    if outcome == "infrastructure_failure":
        return None

    failure_kind = str(case.get("failure_kind") or "").lower()
    if failure_kind == "compile":
        return False
    if failure_kind == "infrastructure":
        return None
    if failure_kind in {"behavior", "runtime"}:
        return True

    spectre = case.get("spectre")
    if isinstance(spectre, dict) and isinstance(spectre.get("ok"), bool):
        if spectre["ok"]:
            return True

    if outcome in {"passed", "behavior_failure", "runtime_failure"}:
        return True
    return None


def _resolve_sidecar_path(score_path: Path, raw_path: object) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = score_path.parent / path
    return path.resolve()


def _case_scs_path(
    sidecar_result_path: Path | None,
    case_id: str,
) -> tuple[Path | None, str | None]:
    if sidecar_result_path is None:
        return None, "audit row has no sidecar_result_path"
    sidecar_dir = sidecar_result_path.parent
    case_dir = sidecar_dir / "cases" / case_id
    candidates = sorted(path for path in case_dir.glob("*.scs") if path.is_file())
    if not candidates and case_dir.is_dir():
        candidates = sorted(path for path in case_dir.rglob("*.scs") if path.is_file())
    if len(candidates) == 1:
        return candidates[0].resolve(), None
    if not candidates:
        return None, f"no .scs artifact found in {case_dir}"
    joined = ", ".join(str(path) for path in candidates)
    return None, f"multiple .scs artifacts found in {case_dir}: {joined}"


def iter_compile_audit_cases(
    score_paths: Sequence[str | Path],
    *,
    transitions_only: bool = False,
) -> Iterator[CompileAuditCase]:
    """Yield case-level target compile labels from Spectre audit score files.

    Row-level target compile failures classify all row cases as expected rejects,
    even if stale per-case labels claim runtime or behavior failure.
    A behavior/runtime failure still proves that Spectre compiled the case.
    Infrastructure failures have no trustworthy compile label and are skipped.
    """
    for score_path_raw in score_paths:
        score_path = Path(score_path_raw).resolve()
        for row in _load_score_rows(score_path):
            if not isinstance(row, dict):
                continue
            source_outcome = str(row.get("source_outcome") or "")
            outcome = str(row.get("outcome") or "")
            source_ok = _source_compile_ok(source_outcome)
            sidecar_result_path = _resolve_sidecar_path(
                score_path,
                row.get("sidecar_result_path"),
            )
            cell_id = str(row.get("cell_id") or "")
            cases = row.get("cases") or []
            for case in cases:
                if not isinstance(case, dict):
                    continue
                expected_ok = _spectre_compile_ok(row, case)
                if expected_ok is None:
                    continue
                if (
                    transitions_only
                    and (source_ok is None or source_ok == expected_ok)
                ):
                    continue
                case_id = str(case.get("case_id") or "")
                if not case_id:
                    continue
                scs_path, discovery_error = _case_scs_path(
                    sidecar_result_path,
                    case_id,
                )
                yield CompileAuditCase(
                    score_path=score_path,
                    cell_id=cell_id,
                    case_id=case_id,
                    scs_path=scs_path,
                    source_outcome=source_outcome,
                    spectre_outcome=outcome,
                    failure_kind=str(case.get("failure_kind") or ""),
                    source_ok=source_ok,
                    expected_ok=expected_ok,
                    discovery_error=discovery_error,
                )


def iter_compile_transition_cases(
    score_paths: Sequence[str | Path],
) -> Iterator[CompileAuditCase]:
    """Yield cases where EVAS's audited compile status differs from Spectre."""
    yield from iter_compile_audit_cases(score_paths, transitions_only=True)


def _count_audit_cases(score_paths: Sequence[str | Path]) -> int:
    total = 0
    for score_path_raw in score_paths:
        for row in _load_score_rows(Path(score_path_raw)):
            if isinstance(row, dict) and isinstance(row.get("cases"), list):
                total += sum(isinstance(case, dict) for case in row["cases"])
    return total


def _diagnostic_to_dict(diagnostic: object) -> dict:
    return {
        "stage": str(getattr(diagnostic, "stage", "")),
        "severity": str(getattr(diagnostic, "severity", "error")),
        "message": str(getattr(diagnostic, "message", diagnostic)),
    }


def replay_compile_audit_scores(
    score_paths: Sequence[str | Path],
    *,
    spectre_strict: bool = True,
    transitions_only: bool = False,
) -> dict:
    results: list[dict] = []
    expected_accept = 0
    expected_reject = 0
    passed = 0
    failed = 0
    input_errors = 0
    compile_exceptions = 0

    cases = list(
        iter_compile_audit_cases(
            score_paths,
            transitions_only=transitions_only,
        )
    )
    for case in cases:
        if case.expected_ok:
            expected_accept += 1
        else:
            expected_reject += 1

        if case.discovery_error is not None or case.scs_path is None:
            input_errors += 1
            failed += 1
            results.append(
                {
                    "cell_id": case.cell_id,
                    "case_id": case.case_id,
                    "score_path": str(case.score_path),
                    "scs_path": None,
                    "source_outcome": case.source_outcome,
                    "spectre_outcome": case.spectre_outcome,
                    "failure_kind": case.failure_kind,
                    "source_ok": case.source_ok,
                    "expected_ok": case.expected_ok,
                    "evas_ok": None,
                    "matched": False,
                    "stage": "replay_input",
                    "diagnostics": [
                        {
                            "stage": "replay_input",
                            "severity": "error",
                            "message": case.discovery_error,
                        }
                    ],
                }
            )
            continue

        try:
            compile_result = compile_spectre_netlist(
                str(case.scs_path),
                spectre_strict=spectre_strict,
            )
        except Exception as exc:
            compile_exceptions += 1
            failed += 1
            results.append(
                {
                    "cell_id": case.cell_id,
                    "case_id": case.case_id,
                    "score_path": str(case.score_path),
                    "scs_path": str(case.scs_path),
                    "source_outcome": case.source_outcome,
                    "spectre_outcome": case.spectre_outcome,
                    "failure_kind": case.failure_kind,
                    "source_ok": case.source_ok,
                    "expected_ok": case.expected_ok,
                    "evas_ok": None,
                    "matched": False,
                    "stage": "replay_exception",
                    "diagnostics": [
                        {
                            "stage": "replay_exception",
                            "severity": "error",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                }
            )
            continue

        matched = bool(compile_result.ok) == case.expected_ok
        if matched:
            passed += 1
        else:
            failed += 1
        results.append(
            {
                "cell_id": case.cell_id,
                "case_id": case.case_id,
                "score_path": str(case.score_path),
                "scs_path": str(case.scs_path),
                "source_outcome": case.source_outcome,
                "spectre_outcome": case.spectre_outcome,
                "failure_kind": case.failure_kind,
                "source_ok": case.source_ok,
                "expected_ok": case.expected_ok,
                "evas_ok": bool(compile_result.ok),
                "matched": matched,
                "stage": str(compile_result.stage),
                "diagnostics": [
                    _diagnostic_to_dict(diagnostic)
                    for diagnostic in compile_result.diagnostics
                ],
            }
        )

    total = len(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "score_paths": [str(Path(path)) for path in score_paths],
        "transitions_only": transitions_only,
        "spectre_strict": spectre_strict,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "expected_accept": expected_accept,
            "expected_reject": expected_reject,
            "skipped_cases": _count_audit_cases(score_paths) - total,
            "input_errors": input_errors,
            "compile_exceptions": compile_exceptions,
        },
        "results": results,
    }


def write_replay_report(report: dict, output_path: str | Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    if output_path is None:
        print(text)
        return
    Path(output_path).write_text(text + "\n", encoding="utf-8")
