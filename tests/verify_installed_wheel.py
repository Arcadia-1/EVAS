"""Verify version identity through an installed wheel's console script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    executable = Path(sys.executable).with_name("evas.exe" if os.name == "nt" else "evas")
    human = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not human.startswith("evas-sim ") or "rust-core" not in human or "ABI" not in human:
        raise SystemExit(f"unexpected human-readable identity: {human!r}")

    raw_json = subprocess.run(
        [str(executable), "--version", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    identity = json.loads(raw_json)
    required = {
        "cli_version",
        "package_version",
        "rust_core_version",
        "rust_core_abi_version",
        "build_revision",
        "rust_core_present",
        "rust_core_loadable",
    }
    missing = sorted(required - identity.keys())
    if missing:
        raise SystemExit(f"identity JSON missing keys: {missing}")
    if identity["cli_version"] != identity["package_version"]:
        raise SystemExit("CLI and package versions disagree")
    if not identity["rust_core_present"] or not identity["rust_core_loadable"]:
        raise SystemExit(f"installed wheel Rust core is not loadable: {identity}")
    if not identity["rust_core_version"] or not isinstance(
        identity["rust_core_abi_version"], int
    ):
        raise SystemExit(f"installed wheel Rust identity is incomplete: {identity}")


if __name__ == "__main__":
    main()
