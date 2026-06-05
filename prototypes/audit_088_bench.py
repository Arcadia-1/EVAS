#!/usr/bin/env python3
"""Audit 088 benchmark: transition operator per-step batch via codegen.

Compares two configurations of the same transition-heavy circuit:

- **086 baseline (immediate)** : the per-call Rust FFI path (with the
  persistent buffer reuse from audit 086). Forced by monkey-patching the
  compiler analyzer to mark every transition output node as unsafe to
  defer, so the codegen emits `_transition_output` (immediate) instead
  of `_transition_output_lazy`.
- **088 batch (deferred)** : current default. The static analyzer
  identifies output nodes that are safe to defer. The compiler emits
  `_transition_output_lazy` for those, and `_flush_transitions` runs
  once at end of evaluate() to make a single Rust FFI call covering all
  transitions in that step.

Both configurations exercise the same Rust math (transition_state_step_for_arrays).
The only difference is how many Rust FFI hops we pay. Parity is checked via
output checksum.

Run:
    PYTHONPATH=. python3 prototypes/audit_088_bench.py
"""
from __future__ import annotations

import statistics
import time

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module, _ModuleCompiler
from evas.simulator.engine import Simulator, ramp, dc


REPEATS = 5
TSTOP = 50e-9
TSTEP = 50e-12
RECORD_STEP = 100e-12


# Three transition contributions in one analog block; no intra-block reads
# of the output nodes → all three are safe to defer in 088.
SRC = """\
`include "disciplines.vams"
module trans_batch_heavy(inp, vdd, vss, o1, o2, o3);
    input voltage inp;
    input voltage vdd;
    input voltage vss;
    output voltage o1;
    output voltage o2;
    output voltage o3;
    integer q1 = 0;
    integer q2 = 0;
    integer q3 = 0;
    analog begin
        q1 = V(inp) > 0.35 ? 1 : 0;
        q2 = V(inp) > 0.50 ? 1 : 0;
        q3 = V(inp) > 0.65 ? 1 : 0;
        V(o1) <+ V(vdd, vss) * transition(q1 ? 1.0 : 0.0, 0.0, 1n, 2n);
        V(o2) <+ V(vdd, vss) * transition(q2 ? 1.0 : 0.0, 0.0, 1n, 2n);
        V(o3) <+ V(vdd, vss) * transition(q3 ? 1.0 : 0.0, 0.0, 1n, 2n);
    end
endmodule
"""


def build_sim(force_immediate: bool):
    if force_immediate:
        # Monkey-patch the analyzer to mark all nodes as unsafe → emits
        # immediate (_transition_output) form for all three transitions.
        orig = _ModuleCompiler._collect_transition_defer_unsafe_nodes
        def patched(self, stmt):
            # Return ALL output nodes as unsafe.
            return {"o1", "o2", "o3"}
        _ModuleCompiler._collect_transition_defer_unsafe_nodes = patched
    try:
        ModelCls = compile_module(parse(SRC))
    finally:
        if force_immediate:
            _ModuleCompiler._collect_transition_defer_unsafe_nodes = orig
    model = ModelCls()
    model.node_map = {
        "inp": "IN", "vdd": "VDD", "vss": "VSS",
        "o1": "O1", "o2": "O2", "o3": "O3",
    }
    sim = Simulator()
    sim.add_source("IN", ramp(0.0, 1.0, 0.0, 1e-9))
    sim.add_source("VDD", dc(0.9))
    sim.add_source("VSS", dc(0.0))
    sim.add_model(model)
    sim.record("O1")
    sim.record("O2")
    sim.record("O3")
    return sim, model


def run_once(force_immediate: bool):
    sim, model = build_sim(force_immediate)
    t0 = time.perf_counter()
    result = sim.run(
        tstop=TSTOP,
        tstep=TSTEP,
        record_step=RECORD_STEP,
        rust_transition_production=True,
        rust_required=True,
    )
    wall = time.perf_counter() - t0
    stats = sim._perf_stats
    return {
        "wall_s": wall,
        "ffi_calls": stats.get("rust_transition_state_production_calls_total", 0),
        "buffer_allocs": stats.get(
            "rust_transition_state_buffer_alloc_grand_total", "absent"
        ),
        "batch_flushes": stats.get("rust_transition_batch_flushes_total", "absent"),
        "batch_slots": stats.get("rust_transition_batch_slot_total_total", "absent"),
        "batch_max_slots": stats.get("rust_transition_batch_max_slots_total", "absent"),
        "lazy_enqueues": stats.get("rust_transition_lazy_enqueues_total", "absent"),
        "batch_fallbacks": stats.get("rust_transition_batch_fallbacks_total", "absent"),
        "checksum_o1": sum(result.signals["O1"]),
        "checksum_o2": sum(result.signals["O2"]),
        "checksum_o3": sum(result.signals["O3"]),
    }


def summarize(label, samples):
    walls = [s["wall_s"] for s in samples]
    s = samples[-1]
    print(f"=== {label} ===")
    print(f"  repeats           = {len(samples)}")
    print(f"  wall median_s     = {statistics.median(walls):.6f}")
    print(f"  wall min_s        = {min(walls):.6f}")
    print(f"  wall max_s        = {max(walls):.6f}")
    print(f"  ffi_calls         = {s['ffi_calls']}")
    print(f"  buffer_allocs     = {s['buffer_allocs']}")
    print(f"  batch_flushes     = {s['batch_flushes']}")
    print(f"  batch_slots_total = {s['batch_slots']}")
    print(f"  batch_max_slots   = {s['batch_max_slots']}")
    print(f"  lazy_enqueues     = {s['lazy_enqueues']}")
    print(f"  batch_fallbacks   = {s['batch_fallbacks']}")
    print(f"  checksum_o1+o2+o3 = {s['checksum_o1'] + s['checksum_o2'] + s['checksum_o3']:.9f}")
    return statistics.median(walls), s


def main():
    run_once(force_immediate=False)  # warm up
    print(f"tstop / tstep / record = {TSTOP:.0e} / {TSTEP:.0e} / {RECORD_STEP:.0e}")
    print(f"REPEATS               = {REPEATS}")
    print(f"Module                = 3 transitions per evaluate()\n")

    imm = [run_once(force_immediate=True) for _ in range(REPEATS)]
    bat = [run_once(force_immediate=False) for _ in range(REPEATS)]

    imm_med, imm_last = summarize("086 immediate (force unsafe; per-call FFI)", imm)
    print()
    bat_med, bat_last = summarize("088 batch (default; one flush per step)", bat)
    print()

    speedup = imm_med / bat_med if bat_med > 0 else float("nan")
    delta_pct = (imm_med - bat_med) / imm_med * 100 if imm_med > 0 else 0.0
    print(f"speedup (086/088)     = {speedup:.3f}x  ({delta_pct:+.2f}% faster)")
    # Real Rust FFI hops: each immediate call is one hop; each batch flush is one hop.
    imm_ffi_hops = imm_last["ffi_calls"]
    bat_ffi_hops = bat_last["batch_flushes"] if isinstance(bat_last["batch_flushes"], int) else 0
    ffi_drop = imm_ffi_hops / max(bat_ffi_hops, 1)
    print(f"FFI hop reduction     = {ffi_drop:.2f}x fewer Rust hops "
          f"({imm_ffi_hops} -> {bat_ffi_hops})")
    avg_slots = (bat_last["batch_slots"] / max(bat_ffi_hops, 1)
                 if isinstance(bat_last["batch_slots"], int) else 0.0)
    print(f"avg transitions/flush = {avg_slots:.2f}")

    parity = (
        abs(imm_last["checksum_o1"] - bat_last["checksum_o1"])
        + abs(imm_last["checksum_o2"] - bat_last["checksum_o2"])
        + abs(imm_last["checksum_o3"] - bat_last["checksum_o3"])
    )
    print(f"parity (sum |diff|)   = {parity:.6e}")
    print(f"parity OK             = {parity < 1e-6}")


if __name__ == "__main__":
    main()
