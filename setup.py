"""Setuptools command hooks for the optional evas-rust shared library."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import Command, setup
from setuptools.command.build_py import build_py as _build_py

try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except Exception:  # pragma: no cover - wheel is only required for wheel builds
    _bdist_wheel = None


RUST_CORE_DIR = Path(__file__).resolve().parent / "evas" / "rust_core"
RUST_LIBRARY_NAMES = {
    "libevas_rust_core.dylib",
    "libevas_rust_core.so",
    "evas_rust_core.dll",
}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _skip_rust_core_build() -> bool:
    return _truthy_env("EVAS_SKIP_RUST_CORE_BUILD")


def _rust_library_filename() -> str:
    if sys.platform == "darwin":
        return "libevas_rust_core.dylib"
    if os.name == "nt":
        return "evas_rust_core.dll"
    return "libevas_rust_core.so"


class build_rust_core(Command):
    description = "build the optional evas-rust shared library with cargo"
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        cargo = shutil.which("cargo")
        if cargo is None:
            raise RuntimeError(
                "cargo is required to build the evas-rust wheel. "
                "Set EVAS_SKIP_RUST_CORE_BUILD=1 to build the pure Python wheel."
            )
        subprocess.run([cargo, "build", "--release"], cwd=RUST_CORE_DIR, check=True)
        library = RUST_CORE_DIR / "target" / "release" / _rust_library_filename()
        if not library.exists():
            raise RuntimeError(f"cargo build did not produce {library}")


class build_py(_build_py):
    def run(self) -> None:
        if not _skip_rust_core_build():
            self.run_command("build_rust_core")
        super().run()

    def find_data_files(self, package, src_dir):
        files = super().find_data_files(package, src_dir)
        if not _skip_rust_core_build():
            return files
        return [
            path
            for path in files
            if Path(path).name not in RUST_LIBRARY_NAMES
        ]


cmdclass = {
    "build_py": build_py,
    "build_rust_core": build_rust_core,
}

if _bdist_wheel is not None:

    class bdist_wheel(_bdist_wheel):
        def finalize_options(self) -> None:
            super().finalize_options()
            self.root_is_pure = _skip_rust_core_build()

        def get_tag(self):
            python_tag, abi_tag, platform_tag = super().get_tag()
            if _skip_rust_core_build():
                return python_tag, abi_tag, platform_tag
            return "py3", "none", platform_tag

    cmdclass["bdist_wheel"] = bdist_wheel


setup(cmdclass=cmdclass)
