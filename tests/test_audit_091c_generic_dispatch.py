"""Unit tests for audit 091c generic-executor dispatch gate inspector.

091c wires a no-op gate inspector into the whole-segment dispatcher chain.
It records, in perf_stats, whether a `generic_event_state_transition_v1`
candidate is reachable and (if not) which gate blocked it. The inspector
intentionally returns None — Python evaluate still runs — so 091c is a
"diagnostic phase" with zero parity risk. 091d will replace the inspector
with an actual segment-trace executor.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc, ramp, pulse


RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


def _build_rust_core_or_skip():
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


GENERIC_SRC = """\
`include "disciplines.vams"
module gen_disp_sample(clk, vdd, vss, o1, o2);
    input voltage clk;
    input voltage vdd;
    input voltage vss;
    output voltage o1;
    output voltage o2;
    integer state = 0;
    integer b1 = 0;
    integer b2 = 0;
    analog begin
        @(initial_step) begin
            state = 0;
            b1 = 0;
            b2 = 0;
        end
        @(cross(V(clk) - 0.45, +1)) begin
            if (state == 0) begin
                b1 = 1;
                state = 1;
            end else if (state == 1) begin
                b2 = 1;
                state = 2;
            end else begin
                state = 0;
                b1 = 0;
                b2 = 0;
            end
        end
        V(o1) <+ V(vdd, vss) * transition(b1 ? 1.0 : 0.0, 0.0, 1n, 2n);
        V(o2) <+ V(vdd, vss) * transition(b2 ? 1.0 : 0.0, 0.0, 1n, 2n);
    end
endmodule
"""


def _build_generic_sim():
    ModelCls = compile_module(parse(GENERIC_SRC))
    model = ModelCls()
    model.node_map = {"clk": "CLK", "vdd": "VDD", "vss": "VSS",
                      "o1": "O1", "o2": "O2"}
    sim = Simulator()
    sim.add_source("VDD", dc(0.9))
    sim.add_source("VSS", dc(0.0))
    sim.add_source("CLK", pulse(
        v_lo=0.0, v_hi=0.9, period=4e-9, duty=0.5,
        rise=100e-12, fall=100e-12,
    ))
    sim.add_model(model)
    sim.record("O1")
    sim.record("O2")
    return sim


class TestDispatchInspector:

    def test_inspector_counts_model_with_candidate(self):
        _build_rust_core_or_skip()
        sim = _build_generic_sim()
        sim.run(
            tstop=8e-9, tstep=100e-12, record_step=100e-12,
            rust_full_model_fastpath=True, rust_required=True,
        )
        stats = sim._perf_stats
        # The model has the candidate, so counter should be > 0.
        assert stats["generic_executor_models_with_candidate"] >= 1, stats
        # No block reason should appear under happy path; dispatch succeeded.
        assert stats["generic_executor_dispatchable_runs"] == 1, stats
        assert stats["generic_executor_blocked_runs"] == 0, stats

    def test_inspector_not_invoked_when_fastpath_disabled(self):
        # Without rust_full_model_fastpath, the dispatcher chain doesn't run.
        sim = _build_generic_sim()
        sim.run(
            tstop=2e-9, tstep=100e-12, record_step=100e-12,
            # rust_full_model_fastpath defaults to False
        )
        stats = sim._perf_stats
        # Counters stay at 0 because dispatcher chain was skipped.
        assert stats["generic_executor_models_with_candidate"] == 0
        assert stats["generic_executor_dispatchable_runs"] == 0

    def test_inspector_returns_none_python_path_still_runs(self):
        # Confirm 091c is non-disruptive: Python evaluate produces the same
        # waveform whether the inspector ran or not.
        _build_rust_core_or_skip()
        ref = _build_generic_sim()
        ref_res = ref.run(
            tstop=8e-9, tstep=100e-12, record_step=100e-12,
        )
        insp = _build_generic_sim()
        insp_res = insp.run(
            tstop=8e-9, tstep=100e-12, record_step=100e-12,
            rust_full_model_fastpath=True, rust_required=True,
        )
        assert list(insp_res.time) == pytest.approx(list(ref_res.time))
        assert list(insp_res.signals["O1"]) == pytest.approx(list(ref_res.signals["O1"]))
        assert list(insp_res.signals["O2"]) == pytest.approx(list(ref_res.signals["O2"]))


class TestNonCandidateModel:

    def test_model_without_candidate_counts_zero(self):
        # A model with no generic candidate should not increment the counter.
        _build_rust_core_or_skip()
        src = """\
`include "disciplines.vams"
module no_cand(inp, out);
    input voltage inp;
    output voltage out;
    analog begin
        V(out) <+ 0.5 * V(inp);
    end
endmodule
"""
        ModelCls = compile_module(parse(src))
        model = ModelCls()
        model.node_map = {"inp": "IN", "out": "O"}
        sim = Simulator()
        sim.add_source("IN", ramp(0.0, 1.0, 0.0, 1e-9))
        sim.add_model(model)
        sim.record("O")
        sim.run(
            tstop=2e-9, tstep=100e-12, record_step=100e-12,
            rust_full_model_fastpath=True, rust_required=True,
        )
        stats = sim._perf_stats
        assert stats["generic_executor_models_with_candidate"] == 0
        assert stats["generic_executor_dispatchable_runs"] == 0
        assert stats["generic_executor_blocked_runs"] == 0
