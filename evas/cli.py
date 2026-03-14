"""EVAS command-line interface."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _get_examples_root() -> Path:
    """Return the path to the bundled examples directory."""
    try:
        from importlib.resources import files
        pkg = files("evas.examples")
        # On Python 3.9-3.10 with zip-safe=false, this returns a real Path
        p = Path(str(pkg))
        if p.is_dir():
            return p
    except Exception:
        pass
    # Fallback: locate relative to this file (editable installs)
    return Path(__file__).parent / "examples"


def _list_examples() -> list[str]:
    root = _get_examples_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def cmd_list(_args: argparse.Namespace) -> int:
    names = _list_examples()
    if not names:
        print("No bundled examples found.", file=sys.stderr)
        return 1
    print(f"Available examples ({len(names)}):")
    for name in names:
        print(f"  {name}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from evas.netlist.runner import evas_simulate
    ok = evas_simulate(args.input, log_path=args.log, output_dir=args.output)
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    from evas.netlist.runner import evas_simulate

    name = args.name
    examples_root = _get_examples_root()
    src = examples_root / name
    if not src.is_dir():
        available = _list_examples()
        print(f"Error: example '{name}' not found.", file=sys.stderr)
        print(f"Available: {', '.join(available)}", file=sys.stderr)
        return 1

    # Copy example files to <cwd>/<name>/
    dst = Path.cwd() / name
    dst.mkdir(parents=True, exist_ok=True)

    for f in src.iterdir():
        if f.is_file() and not f.name.startswith("_"):
            shutil.copy2(f, dst / f.name)

    # Determine which .scs to run
    if args.tb:
        scs_file = dst / args.tb
        if not scs_file.exists():
            print(f"Error: testbench '{args.tb}' not found in {dst}", file=sys.stderr)
            return 1
    else:
        # Prefer tb_<name>.scs; fall back to any single .scs
        preferred = dst / f"tb_{name}.scs"
        if preferred.exists():
            scs_file = preferred
        else:
            scs_files = sorted(dst.glob("*.scs"))
            if not scs_files:
                print(f"Error: no .scs testbench found in {dst}", file=sys.stderr)
                return 1
            scs_file = scs_files[0]

    output_dir = Path.cwd() / "output" / name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running example '{name}': {scs_file.name} → {output_dir}")
    env_backup = os.environ.get("EVAS_OUTPUT_DIR")
    os.environ["EVAS_OUTPUT_DIR"] = str(output_dir)

    try:
        ok = evas_simulate(str(scs_file), output_dir=str(output_dir))
    finally:
        if env_backup is None:
            os.environ.pop("EVAS_OUTPUT_DIR", None)
        else:
            os.environ["EVAS_OUTPUT_DIR"] = env_backup

    if not ok:
        return 1

    # Run analyze_<name>.py if present
    analyze = dst / f"analyze_{name}.py"
    if analyze.exists():
        import subprocess
        print(f"Running analysis: {analyze.name}")
        result = subprocess.run(
            [sys.executable, str(analyze)],
            cwd=str(dst),
            env={**os.environ, "EVAS_OUTPUT_DIR": str(output_dir)},
        )
        return result.returncode

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="evas",
        description="EVAS — Event-driven Verilog-A Simulator",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # evas simulate
    p_sim = sub.add_parser("simulate", help="Simulate a Spectre .scs netlist")
    p_sim.add_argument("input", help=".scs netlist file")
    p_sim.add_argument("-o", "--output", default="./output",
                       help="Output directory (default: ./output)")
    p_sim.add_argument("-log", help="Log file path")
    p_sim.set_defaults(func=cmd_simulate)

    # evas run
    p_run = sub.add_parser("run", help="Run a bundled example")
    p_run.add_argument("name", help="Example name (see 'evas list')")
    p_run.add_argument("--tb", metavar="FILE",
                       help="Testbench filename override (default: tb_<name>.scs)")
    p_run.set_defaults(func=cmd_run)

    # evas list
    p_list = sub.add_parser("list", help="List all available examples")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
