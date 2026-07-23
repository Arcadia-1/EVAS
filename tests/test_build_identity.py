"""Build identity and provenance metadata tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evas.build_identity import (
    collect_build_identity,
    package_version,
    write_build_identity,
)
from evas.simulator.rust_backend import EXPECTED_RUST_CORE_ABI_VERSION

RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


def _build_rust_core_or_skip() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def test_build_identity_reports_loaded_core_metadata():
    _build_rust_core_or_skip()

    identity = collect_build_identity()

    assert identity["schema_version"] == 1
    assert identity["cli_version"] == package_version()
    assert identity["package_version"] == package_version()
    assert identity["engine"] == "evas-rust"
    assert identity["rust_core_version"] == "0.2.1"
    assert identity["rust_core_abi_version"] == EXPECTED_RUST_CORE_ABI_VERSION
    assert identity["rust_core_present"] is True
    assert identity["rust_core_loadable"] is True


def test_build_identity_reports_missing_core_without_raising(tmp_path):
    missing = tmp_path / "missing-rust-core.so"

    identity = collect_build_identity(missing)

    assert identity["rust_core_present"] is False
    assert identity["rust_core_loadable"] is False
    assert identity["rust_core_version"] is None
    assert identity["rust_core_abi_version"] is None
    assert "not found" in identity["rust_core_error"]


def test_write_build_identity_is_deterministic_json(tmp_path):
    path = tmp_path / "evas_identity.json"
    identity = {
        "schema_version": 1,
        "package_version": "1.2.3",
        "rust_core_loadable": True,
    }

    write_build_identity(path, identity)

    assert json.loads(path.read_text(encoding="utf-8")) == identity
    assert path.read_text(encoding="utf-8").endswith("\n")
