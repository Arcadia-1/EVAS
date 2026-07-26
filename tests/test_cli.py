"""CLI entrypoint regression tests."""

import json
import runpy
import sys

import pytest


def test_python_m_evas_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evas", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "EVAS" in out
    assert "simulate" in out


def test_evas_version_human_readable(monkeypatch, capsys):
    monkeypatch.setattr(
        "evas.cli.collect_build_identity",
        lambda: {
            "package_version": "9.8.7",
            "rust_core_version": "6.5.4",
            "rust_core_abi_version": 20260718,
            "build_revision": "abc123",
            "rust_core_loadable": True,
        },
    )
    monkeypatch.setattr(sys, "argv", ["evas", "--version"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out == (
        "evas-sim 9.8.7 "
        "(rust-core 6.5.4, ABI 20260718, revision abc123, loadable)"
    )


def test_evas_version_json(monkeypatch, capsys):
    identity = {
        "schema_version": 1,
        "cli_version": "9.8.7",
        "package_name": "evas-sim",
        "package_version": "9.8.7",
        "engine": "evas-rust",
        "rust_core_version": "6.5.4",
        "rust_core_abi_version": 20260718,
        "build_revision": "abc123",
        "rust_core_present": True,
        "rust_core_loadable": True,
    }
    monkeypatch.setattr("evas.cli.collect_build_identity", lambda: identity)
    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "--version", "--format", "json"],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 0
    assert json.loads(capsys.readouterr().out) == identity


def test_cli_rejects_python_simulation_engine(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "simulate", "tb.scs", "--engine", "python"],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 2
    assert "invalid choice: 'python'" in capsys.readouterr().err


def test_cli_compile_audit_replay_returns_zero_when_transitions_match(
    tmp_path,
    monkeypatch,
    capsys,
):
    sidecar = tmp_path / "cells" / "c1" / "input" / "result.json"
    case_dir = sidecar.parent / "cases" / "score"
    case_dir.mkdir(parents=True)
    scs = case_dir / "c1_score__tb_score.scs"
    scs.write_text(
        "simulator lang=spectre\n"
        "global 0\n"
        "V1 (out 0) vsource dc=1\n"
        "save out\n",
        encoding="utf-8",
    )
    sidecar.write_text("{}", encoding="utf-8")
    score = tmp_path / "score.json"
    score.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "cell_id": "c1",
                        "source_outcome": "compile_failure",
                        "outcome": "passed",
                        "sidecar_result_path": str(sidecar),
                        "cases": [
                            {
                                "case_id": "score",
                                "failure_kind": None,
                                "spectre": {"ok": True},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "compile-audit-replay", str(score)],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 0
    assert json.loads(capsys.readouterr().out)["summary"]["failed"] == 0


def test_cli_compile_audit_replay_returns_one_when_transition_mismatches(
    tmp_path,
    monkeypatch,
    capsys,
):
    sidecar = tmp_path / "cells" / "c1" / "input" / "result.json"
    case_dir = sidecar.parent / "cases" / "score"
    case_dir.mkdir(parents=True)
    (case_dir / "c1_score__tb_score.scs").write_text(
        "simulator lang=spectre\n"
        "global 0\n"
        "V1 (out 0) vsource dc=1\n"
        "save out\n",
        encoding="utf-8",
    )
    sidecar.write_text("{}", encoding="utf-8")
    score = tmp_path / "score.json"
    score.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "cell_id": "c1",
                        "source_outcome": "passed",
                        "outcome": "compile_failure",
                        "sidecar_result_path": str(sidecar),
                        "cases": [
                            {
                                "case_id": "score",
                                "failure_kind": "compile",
                                "spectre": {"ok": False},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "compile-audit-replay", str(score), "--output", str(output)],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == ""
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["failed"] == 1
