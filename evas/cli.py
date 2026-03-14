"""EVAS command-line interface."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _get_examples_root() -> Path:
    """Return the bundled examples directory."""
    try:
        from importlib.resources import files
        p = Path(str(files("evas.examples")))
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path(__file__).parent / "examples"


def _list_examples() -> list[str]:
    root = _get_examples_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def _pick_scs(dst: Path, name: str, tb: str | None) -> Path | None:
    """Return the .scs file to simulate, or None on error."""
    if tb:
        scs_file = dst / tb
        if not scs_file.exists():
            print(f"Error: testbench '{tb}' not found in {dst}", file=sys.stderr)
            return None
        return scs_file
    preferred = dst / f"tb_{name}.scs"
    if preferred.exists():
        return preferred
    scs_files = sorted(dst.glob("*.scs"))
    if not scs_files:
        print(f"Error: no .scs testbench found in {dst}", file=sys.stderr)
        return None
    return scs_files[0]


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

    # Copy example files to <cwd>/evas-run/<name>/
    dst = Path.cwd() / "evas-run" / name
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and not f.name.startswith("_"):
            shutil.copy2(f, dst / f.name)

    # Simulate directly — no subprocess, no env vars
    output_dir = Path.cwd() / "evas-run" / "output" / name
    output_dir.mkdir(parents=True, exist_ok=True)

    scs_file = _pick_scs(dst, name, args.tb)
    if scs_file is None:
        return 1

    print(f"Running example '{name}': {scs_file.name} → {output_dir}")
    ok = evas_simulate(str(scs_file), output_dir=str(output_dir))
    if ok:
        print(f"Output: {output_dir}")
    return 0 if ok else 1


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
