#!/usr/bin/env python3
"""Audit 086 benchmark: transition operator persistent buffer reuse.

Compares two configurations of the same transition-heavy circuit:

- **baseline** : forces `_rust_transition_buffers = None` before every
  `_transition_rust_production()` call, recreating the 14 typed-array
  buffers on every FFI invocation (the pre-086 behavior).
- **new (086)** : leaves the persistent buffer in place, so only the
  first `_transition_rust_production()` call allocates and every
  subsequent call reuses the same buffers.

Both configurations exercise the same Rust FFI; the only difference is
how often Python allocates `array("d", [...])`. Parity is checked via
output checksum.

Run:
    PYTHONPATH=. python3 prototypes/audit_086_bench.py
"""
from __future__ import annotations

import statistics
import time

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, ramp


REPEATS = 5
TSTOP = 50e-9
TSTEP = 50e-12
RECORD_STEP = 100e-12


SRC = """\
`include "disciplines.vams"
module trans_state_heavy(inp, out);
    input voltage inp;
    output voltage out;
    integer q = 0;
    analog begin
        q = V(inp) > 0.45 ? 1 : 0;
        V(out) <+ transition(q ? 1.0 : 0.0, 0.0, 1n, 2n);
    end
endmodule
"""


def build_sim():
    ModelCls = compile_module(parse(SRC))
    model = ModelCls()
    model.node_map = {"inp": "IN", "out": "OUT"}
    sim = Simulator()
    sim.add_source("IN", ramp(0.0, 1.0, 0.0, 1e-9))
    sim.add_model(model)
    sim.record("OUT")
    return sim, model


def patch_force_realloc(sim_models):
    """Force every _transition_rust_production() call to reallocate
    the typed-array buffers (simulates pre-086 baseline)."""
    for model in sim_models:
        original = type(model)._transition_rust_production

        def wrapper(self, *args, _orig=original, **kwargs):
            self._rust_transition_buffers = None
            return _orig(self, *args, **kwargs)

        model._transition_rust_production = wrapper.__get__(model, type(model))


def run_once(force_baseline: bool):
    sim, model = build_sim()
    if force_baseline:
        patch_force_realloc([model])
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
        "buffer_reuse_calls": stats.get(
            "rust_transition_state_buffer_reuse_calls_total", "absent"
        ),
        "checksum": sum(result.signals["OUT"]),
    }


def summarize(label, samples):
    walls = [s["wall_s"] for s in samples]
    print(f"=== {label} ===")
    print(f"  repeats            = {len(samples)}")
    print(f"  wall median_s      = {statistics.median(walls):.6f}")
    print(f"  wall min_s         = {min(walls):.6f}")
    print(f"  wall max_s         = {max(walls):.6f}")
    print(f"  ffi_calls          = {samples[-1]['ffi_calls']}")
    print(f"  buffer_allocs      = {samples[-1]['buffer_allocs']}")
    print(f"  buffer_reuse_calls = {samples[-1]['buffer_reuse_calls']}")
    print(f"  output_checksum    = {samples[-1]['checksum']:.9f}")
    return statistics.median(walls), samples[-1]


def main():
    # Warm up rust_core / compiler
    run_once(force_baseline=False)
    print(f"tstop / tstep / record = {TSTOP:.0e} / {TSTEP:.0e} / {RECORD_STEP:.0e}")
    print(f"REPEATS               = {REPEATS}\n")

    baseline_samples = [run_once(force_baseline=True) for _ in range(REPEATS)]
    new_samples = [run_once(force_baseline=False) for _ in range(REPEATS)]

    base_med, base_last = summarize("baseline (force per-call realloc)", baseline_samples)
    print()
    new_med, new_last = summarize("audit 086 (persistent buffer reuse)", new_samples)
    print()

    speedup = base_med / new_med if new_med > 0 else float("nan")
    delta_pct = (base_med - new_med) / base_med * 100 if base_med > 0 else 0.0
    print(f"speedup (baseline/new) = {speedup:.3f}x  ({delta_pct:+.2f}% faster)")

    parity_ok = abs(base_last["checksum"] - new_last["checksum"]) < 1e-6
    print(f"parity (checksum diff) = {abs(base_last['checksum'] - new_last['checksum']):.6e}")
    print(f"parity OK              = {parity_ok}")

    alloc_ratio = base_last["buffer_allocs"] / new_last["buffer_allocs"] if new_last["buffer_allocs"] else float("inf")
    print(f"alloc reduction        = {alloc_ratio:.1f}x fewer array() allocations")


if __name__ == "__main__":
    main()
