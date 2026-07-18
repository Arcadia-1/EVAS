"""Stable package and Rust-core build identity reporting."""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Any, Optional, Union

PACKAGE_NAME = "evas-sim"
CANONICAL_ENGINE = "evas-rust"
IDENTITY_SCHEMA_VERSION = 1


def package_version() -> str:
    """Return the installed package version without a duplicated fallback."""
    # A source checkout can coexist with an older installed wheel. Prefer the
    # adjacent project metadata when it exists so `python -m evas --version`
    # identifies the code actually being executed.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "unknown"


def _direct_url_revision() -> Optional[str]:
    """Return a PEP 610 VCS revision when installed from a repository."""
    try:
        dist = distribution(PACKAGE_NAME)
        direct_url = dist.read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return None
    if not direct_url:
        return None
    try:
        payload = json.loads(direct_url)
    except (TypeError, ValueError):
        return None
    revision = payload.get("vcs_info", {}).get("commit_id")
    return str(revision) if revision else None


def collect_build_identity(
    rust_library: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """Collect a machine-readable identity without raising on a missing core."""
    from evas.simulator.rust_backend import (
        RustBackendError,
        default_rust_core_library_path,
        load_rust_backend,
    )

    configured_library = rust_library
    if configured_library is None:
        configured_library = os.environ.get("EVAS_RUST_CORE_LIB")
    candidate = (
        Path(configured_library)
        if configured_library
        else default_rust_core_library_path()
    )
    present = candidate.is_file()
    loadable = False
    core_version: Optional[str] = None
    abi_version: Optional[int] = None
    build_revision = _direct_url_revision()
    load_error: Optional[str] = None

    try:
        backend = load_rust_backend(candidate)
    except (OSError, RustBackendError) as exc:
        load_error = str(exc)
    else:
        loadable = True
        core_version = backend.core_version
        abi_version = backend.abi_version
        if backend.build_revision:
            build_revision = backend.build_revision

    identity: dict[str, Any] = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "cli_version": package_version(),
        "package_name": PACKAGE_NAME,
        "package_version": package_version(),
        "engine": CANONICAL_ENGINE,
        "rust_core_version": core_version,
        "rust_core_abi_version": abi_version,
        "build_revision": build_revision,
        "rust_core_present": present,
        "rust_core_loadable": loadable,
    }
    if load_error:
        identity["rust_core_error"] = load_error
    return identity


def format_build_identity(identity: dict[str, Any]) -> str:
    """Format a concise human-readable version line."""
    core_version = identity.get("rust_core_version") or "unknown"
    abi_version = identity.get("rust_core_abi_version")
    abi_text = str(abi_version) if abi_version is not None else "unknown"
    revision = identity.get("build_revision") or "unknown"
    state = "loadable" if identity.get("rust_core_loadable") else "unavailable"
    return (
        f"evas-sim {identity['package_version']} "
        f"(rust-core {core_version}, ABI {abi_text}, revision {revision}, {state})"
    )


def write_build_identity(path: Union[str, Path], identity: dict[str, Any]) -> None:
    """Write deterministic run metadata for benchmark provenance."""
    Path(path).write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
