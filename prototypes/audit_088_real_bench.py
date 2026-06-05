#!/usr/bin/env python3
"""Audit 088 verification on a real EVAS workload.

Uses the bundled `comparator/cmp_delay` example — the same Verilog-A
module that vabench's top-wall `vbr1_l1_propagation_delay_comparator`
row exercises. It is a real comparator with cross() detectors and two
transition() outputs (DCMPP, DCMPN), so it exercises both the static
analyzer (cross + transition mix) and the per-step batch path.

Two modes:
- **086 immediate (forced)** : analyzer monkey-patched to flag every
  transition output node as unsafe → emits the old immediate form.
- **088 batch (default)** : current behavior — safe nodes get lazy
  emission, one Rust FFI per evaluate end.

Run:
    PYTHONPATH=. python3 prototypes/audit_088_real_bench.py
"""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from evas.netlist.runner import evas_simulate
from evas.simulator.engine import Simulator
from evas.simulator.backend import _ModuleCompiler


REPEATS = 5
HERE = Path(__file__).resolve().parent
SCS_PATH = HERE.parent / "evas" / "examples" / "comparator" / "tb_cmp_delay.scs"


def run_once(force_immediate: bool):
    if force_immediate:
        orig = _ModuleCompiler._collect_transition_defer_unsafe_nodes
        def patched(self, stmt):
            # Force every transition LHS into the unsafe set.
            timeline = []
            self._collect_transition_defer_timeline(stmt, timeline)
            return {node for kind, node in timeline if kind == "transition_write"}
        _ModuleCompiler._collect_transition_defer_unsafe_nodes = patched

    # Capture the Simulator object inside evas_simulate by hooking Simulator.run.
    captured = []
    orig_run = Simulator.run
    def wrap_run(self, *a, **kw):
        captured.append(self)
        # Force rust_transition_production opt-in for this verification.
        kw["rust_transition_production"] = True
        kw["rust_required"] = True
        return orig_run(self, *a, **kw)
    Simulator.run = wrap_run

    try:
        with tempfile.TemporaryDirectory() as tmpd:
            t0 = time.perf_counter()
            ok = evas_simulate(
                str(SCS_PATH),
                log_path=str(Path(tmpd) / "sim.log"),
                output_dir=tmpd,
            )
            wall = time.perf_counter() - t0
            if not ok:
                raise RuntimeError("evas_simulate returned False")
    finally:
        Simulator.run = orig_run
        if force_immediate:
            _ModuleCompiler._collect_transition_defer_unsafe_nodes = orig

    if not captured:
        raise RuntimeError("Simulator.run was not invoked")
    sim = captured[0]
    stats = sim._perf_stats
    return {
        "wall_s": wall,
        "ffi_calls": stats.get("rust_transition_state_production_calls_total", 0),
        "buffer_allocs": stats.get(
            "rust_transition_state_buffer_alloc_grand_total", 0
        ),
        "batch_flushes": stats.get("rust_transition_batch_flushes_total", 0),
        "batch_slots": stats.get("rust_transition_batch_slot_total_total", 0),
        "batch_max_slots": stats.get("rust_transition_batch_max_slots_total", 0),
        "lazy_enqueues": stats.get("rust_transition_lazy_enqueues_total", 0),
        "batch_fallbacks": stats.get("rust_transition_batch_fallbacks_total", 0),
        "transition_calls": stats.get("transition_calls_total", 0),
        "transition_output_fastpath_calls": stats.get(
            "transition_output_fastpath_calls_total", 0
        ),
    }


def summarize(label, samples):
    walls = [s["wall_s"] for s in samples]
    s = samples[-1]
    print(f"=== {label} ===")
    print(f"  repeats                 = {len(samples)}")
    print(f"  wall median_s           = {statistics.median(walls):.6f}")
    print(f"  wall min_s              = {min(walls):.6f}")
    print(f"  wall max_s              = {max(walls):.6f}")
    print(f"  transition_calls        = {s['transition_calls']}")
    print(f"  transition_fastpath_calls = {s['transition_output_fastpath_calls']}")
    print(f"  rust FFI hops (calls)   = {s['ffi_calls']}")
    print(f"  rust batch_flushes      = {s['batch_flushes']}")
    print(f"  rust batch_slot_total   = {s['batch_slots']}")
    print(f"  rust batch_max_slots    = {s['batch_max_slots']}")
    print(f"  rust lazy_enqueues      = {s['lazy_enqueues']}")
    print(f"  rust batch_fallbacks    = {s['batch_fallbacks']}")
    print(f"  rust buffer_allocs      = {s['buffer_allocs']}")
    return statistics.median(walls), s


def main():
    if not SCS_PATH.exists():
        raise SystemExit(f"missing testbench: {SCS_PATH}")
    print(f"workload   : {SCS_PATH}")
    print(f"module     : cmp_delay (2 transition outputs, 2 cross detectors)")
    print(f"repeats    : {REPEATS}\n")

    # Warm up
    run_once(force_immediate=False)

    imm = [run_once(force_immediate=True) for _ in range(REPEATS)]
    bat = [run_once(force_immediate=False) for _ in range(REPEATS)]

    imm_med, imm_last = summarize("086 immediate (force unsafe; per-call FFI)", imm)
    print()
    bat_med, bat_last = summarize("088 batch (default; one flush per evaluate)", bat)
    print()

    speedup = imm_med / bat_med if bat_med > 0 else float("nan")
    delta_pct = (imm_med - bat_med) / imm_med * 100 if imm_med > 0 else 0.0
    print(f"speedup (086 / 088)       = {speedup:.3f}x  ({delta_pct:+.2f}% faster)")
    if bat_last["batch_flushes"] > 0:
        ffi_drop = imm_last["ffi_calls"] / bat_last["batch_flushes"]
        avg_slots = bat_last["batch_slots"] / bat_last["batch_flushes"]
        print(f"FFI hop reduction         = {ffi_drop:.2f}x ({imm_last['ffi_calls']} -> {bat_last['batch_flushes']})")
        print(f"avg transitions per flush = {avg_slots:.2f}")
    else:
        print("(088 batch path not triggered — analyzer marked all unsafe?)")


if __name__ == "__main__":
    main()
