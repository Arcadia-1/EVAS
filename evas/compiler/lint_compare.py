"""Compare Cadence AHDL lint oracle output with EVAS lint diagnostics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from evas.compiler.linter import LINT_RULE_SPECS

DEFAULT_NOISE_CODES = frozenset({"VACOMP-2435"})


@dataclass(frozen=True)
class CadenceLintCase:
    case_id: str
    status: str
    codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EvasLintCase:
    case_id: str
    status: str
    codes: frozenset[str] = frozenset()
    spectre_ids: frozenset[str] = frozenset()
    spectre_id_groups: tuple[frozenset[str], ...] = ()
    severities: frozenset[str] = frozenset()


@dataclass
class LintComparison:
    case_id: str
    category: str
    cadence_status: str = "missing"
    evas_status: str = "missing"
    cadence_codes: list[str] = field(default_factory=list)
    evas_codes: list[str] = field(default_factory=list)
    evas_spectre_ids: list[str] = field(default_factory=list)
    matched_spectre_ids: list[str] = field(default_factory=list)
    missing_cadence_codes: list[str] = field(default_factory=list)
    extra_evas_spectre_ids: list[str] = field(default_factory=list)
    evas_specific_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_cadence_cases(
    payload: Mapping[str, Any],
    *,
    ignore_codes: Iterable[str] = DEFAULT_NOISE_CODES,
) -> dict[str, CadenceLintCase]:
    """Extract compact Cadence lint cases from a local oracle summary JSON."""
    ignored = set(ignore_codes)
    cases: dict[str, CadenceLintCase] = {}
    for item in payload.get("cases", []):
        if not isinstance(item, Mapping):
            continue
        case_id = _first_string(item, "name", "id", "case_id")
        if not case_id:
            continue
        raw_counts = item.get("code_counts", {})
        codes = {
            str(code)
            for code in raw_counts
            if isinstance(code, str) and code not in ignored
        }
        status = str(item.get("status") or ("success" if item.get("ok") else "failure"))
        cases[case_id] = CadenceLintCase(
            case_id=case_id,
            status=status,
            codes=frozenset(sorted(codes)),
        )
    return cases


def extract_evas_cases(payload: Any) -> dict[str, EvasLintCase]:
    """Extract EVAS lint cases from v3 preflight or plain diagnostic JSON."""
    if isinstance(payload, Mapping) and isinstance(payload.get("rows"), list):
        return {
            case.case_id: case
            for case in (_extract_evas_row(row) for row in payload["rows"])
            if case is not None
        }
    if isinstance(payload, Mapping) and isinstance(payload.get("cases"), list):
        return {
            case.case_id: case
            for case in (_extract_evas_row(row) for row in payload["cases"])
            if case is not None
        }
    if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
        if all("code" in item for item in payload):
            return {"input": _case_from_diagnostics("input", "unknown", payload)}
        return {
            case.case_id: case
            for case in (_extract_evas_row(row) for row in payload)
            if case is not None
        }
    return {}


def compare_lint_cases(
    cadence_cases: Mapping[str, CadenceLintCase],
    evas_cases: Mapping[str, EvasLintCase],
) -> list[LintComparison]:
    """Compare Cadence-observed codes with EVAS diagnostics by case id."""
    comparisons: list[LintComparison] = []
    for case_id in sorted(set(cadence_cases) | set(evas_cases)):
        cadence = cadence_cases.get(case_id)
        evas = evas_cases.get(case_id)
        cadence_codes = set(cadence.codes if cadence else ())
        evas_codes = set(evas.codes if evas else ())
        evas_spectre_ids = set(evas.spectre_ids if evas else ())
        matched, extra_spectre = _matched_and_extra_spectre_ids(
            cadence_codes,
            evas.spectre_id_groups if evas else (),
        )
        missing = cadence_codes - matched
        evas_specific = {
            code
            for code in evas_codes
            if not _registry_spectre_ids(code)
        }
        category = _comparison_category(cadence, evas, missing, extra_spectre)
        comparisons.append(
            LintComparison(
                case_id=case_id,
                category=category,
                cadence_status=cadence.status if cadence else "missing",
                evas_status=evas.status if evas else "missing",
                cadence_codes=sorted(cadence_codes),
                evas_codes=sorted(evas_codes),
                evas_spectre_ids=sorted(evas_spectre_ids),
                matched_spectre_ids=sorted(matched),
                missing_cadence_codes=sorted(missing),
                extra_evas_spectre_ids=sorted(extra_spectre),
                evas_specific_codes=sorted(evas_specific),
            )
        )
    return comparisons


def summarize_comparisons(comparisons: Sequence[LintComparison]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for comparison in comparisons:
        counts[comparison.category] = counts.get(comparison.category, 0) + 1
    return {
        "cases_total": len(comparisons),
        "categories": dict(sorted(counts.items())),
        "missing_cadence_code_total": sum(len(c.missing_cadence_codes) for c in comparisons),
        "extra_evas_spectre_id_total": sum(len(c.extra_evas_spectre_ids) for c in comparisons),
        "evas_specific_code_total": sum(len(c.evas_specific_codes) for c in comparisons),
    }


def format_markdown(comparisons: Sequence[LintComparison]) -> str:
    lines = [
        "| Case | Category | Cadence codes | EVAS codes | Missing Cadence codes | Extra EVAS Spectre IDs |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in comparisons:
        lines.append(
            "| {case} | {category} | {cadence} | {evas} | {missing} | {extra} |".format(
                case=item.case_id,
                category=item.category,
                cadence=", ".join(item.cadence_codes) or "-",
                evas=", ".join(item.evas_codes) or "-",
                missing=", ".join(item.missing_cadence_codes) or "-",
                extra=", ".join(item.extra_evas_spectre_ids) or "-",
            )
        )
    return "\n".join(lines) + "\n"


def _comparison_category(
    cadence: CadenceLintCase | None,
    evas: EvasLintCase | None,
    missing: set[str],
    extra_spectre: set[str],
) -> str:
    if cadence is None:
        return "evas_only"
    if evas is None:
        return "cadence_only"
    cadence_failed = cadence.status.lower() not in {"success", "pass", "ok"}
    evas_compat = "compat-error" in evas.severities or evas.status == "FAIL_COMPAT"
    if cadence_failed and not evas_compat:
        return "cadence_failure_without_evas_compat"
    if not cadence_failed and evas_compat:
        return "evas_false_compat_error"
    if missing and extra_spectre:
        return "classification_mismatch"
    if missing:
        return "evas_missing_cadence_code"
    if extra_spectre:
        return "evas_extra_cadence_mapped_code"
    return "aligned"


def _extract_evas_row(row: Mapping[str, Any]) -> EvasLintCase | None:
    case_id = _first_string(row, "case_id", "id", "name", "task_slug")
    if not case_id:
        return None
    diagnostics = row.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        diagnostics = []
    status = str(row.get("status") or "unknown")
    return _case_from_diagnostics(case_id, status, diagnostics)


def _case_from_diagnostics(
    case_id: str,
    status: str,
    diagnostics: Sequence[Any],
) -> EvasLintCase:
    codes: set[str] = set()
    spectre_ids: set[str] = set()
    spectre_id_groups: list[frozenset[str]] = []
    severities: set[str] = set()
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            continue
        diagnostic_spectre_ids: set[str] = set()
        code = diagnostic.get("code")
        if isinstance(code, str):
            codes.add(code)
            diagnostic_spectre_ids.update(_registry_spectre_ids(code))
        severity = diagnostic.get("severity")
        if isinstance(severity, str):
            severities.add(severity)
        raw_spectre_ids = diagnostic.get("spectre_ids", [])
        if isinstance(raw_spectre_ids, list):
            diagnostic_spectre_ids.update(
                str(item) for item in raw_spectre_ids if isinstance(item, str)
            )
        if diagnostic_spectre_ids:
            group = frozenset(sorted(diagnostic_spectre_ids))
            spectre_id_groups.append(group)
            spectre_ids.update(group)
    return EvasLintCase(
        case_id=case_id,
        status=status,
        codes=frozenset(sorted(codes)),
        spectre_ids=frozenset(sorted(spectre_ids)),
        spectre_id_groups=tuple(spectre_id_groups),
        severities=frozenset(sorted(severities)),
    )


def _first_string(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _registry_spectre_ids(code: str) -> tuple[str, ...]:
    spec = LINT_RULE_SPECS.get(code)
    return spec.spectre_ids if spec is not None else ()


def _matched_and_extra_spectre_ids(
    cadence_codes: set[str],
    evas_spectre_id_groups: Sequence[frozenset[str]],
) -> tuple[set[str], set[str]]:
    """Compare by diagnostic groups to avoid false extras for multi-ID rules."""
    matched: set[str] = set()
    extra: set[str] = set()
    for group in evas_spectre_id_groups:
        overlap = set(group) & cadence_codes
        if overlap:
            matched.update(overlap)
        else:
            extra.update(group)
    return matched, extra
