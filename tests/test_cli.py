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
