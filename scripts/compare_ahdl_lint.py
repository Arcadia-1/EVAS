#!/usr/bin/env python3
"""Compare Cadence AHDL oracle JSON with EVAS lint JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from evas.compiler.lint_compare import (
        DEFAULT_NOISE_CODES,
        compare_lint_cases,
        extract_cadence_cases,
        extract_evas_cases,
        format_markdown,
        summarize_comparisons,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--cadence-summary", required=True)
    parser.add_argument("--evas-report", required=True)
    parser.add_argument(
        "--ignore-cadence-code",
        action="append",
        default=[],
        help="Cadence code to ignore; defaults include environment-noise codes.",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    cadence_payload = json.loads(Path(args.cadence_summary).read_text(encoding="utf-8"))
    evas_payload = json.loads(Path(args.evas_report).read_text(encoding="utf-8"))
    ignore_codes = set(DEFAULT_NOISE_CODES) | set(args.ignore_cadence_code)

    comparisons = compare_lint_cases(
        extract_cadence_cases(cadence_payload, ignore_codes=ignore_codes),
        extract_evas_cases(evas_payload),
    )
    if args.format == "markdown":
        text = format_markdown(comparisons)
    else:
        text = json.dumps(
            {
                "summary": summarize_comparisons(comparisons),
                "comparisons": [item.to_dict() for item in comparisons],
            },
            indent=2,
            sort_keys=True,
        ) + "\n"

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
