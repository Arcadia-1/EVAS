"""Regression coverage for case-selected transition contributions."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.netlist.runner import _validate_va_spectre_compat
from evas.simulator.backend import CompilationError, compile_module
from evas.simulator.engine import Simulator, dc, pwl


RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


@pytest.fixture(scope="module", autouse=True)
def _rust_core() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def test_case_selected_transitions_publish_selected_output() -> None:
    """Family 006: a discrete case selector drives the selected transition."""

    src = """\
`include "disciplines.vams"
module case_transition(clk, rst_n, out0, out1);
    input voltage clk, rst_n;
    output voltage out0, out1;
    integer state;
    analog begin
        @(initial_step) state = 0;
        @(cross(V(clk) - 0.45, +1)) begin
            if (V(rst_n) > 0.45)
                state = 1;
        end
        case (state)
            0: begin
                V(out0) <+ transition(0.9, 0, 200p);
                V(out1) <+ transition(0.0, 0, 200p);
            end
            1: begin
                V(out0) <+ transition(0.0, 0, 200p);
                V(out1) <+ transition(0.9, 0, 200p);
            end
        endcase
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    assert model_cls._transition_target_ir_ops == ()
    assert model_cls._ordered_transition_segment_ir_ops == ((), ())
    model = model_cls()
    model.node_map = {
        "clk": "clk",
        "rst_n": "rst_n",
        "out0": "out0",
        "out1": "out1",
    }

    sim = Simulator()
    sim.add_source(
        "clk",
        pwl([0.0, 0.9e-9, 1.1e-9, 3e-9], [0.0, 0.0, 0.9, 0.9]),
    )
    sim.add_source("rst_n", dc(0.9))
    sim.add_model(model)
    sim.record("out0")
    sim.record("out1")

    result = sim.run(
        tstop=3e-9,
        tstep=500e-12,
        record_step=100e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert sim._perf_stats["rust_sim_program_transition_count"] == 2
    assert sim._perf_stats["rust_sim_program_rejections"] == 0
    early = (result.time >= 0.3e-9) & (result.time <= 0.8e-9)
    late = result.time >= 1.5e-9
    assert result.signals["out0"][early].min() > 0.8
    assert result.signals["out1"][early].max() < 0.1
    assert result.signals["out0"][late].max() < 0.1
    assert result.signals["out1"][late].min() > 0.8


@pytest.mark.parametrize(
    "state_write",
    [
        "",
        "@(initial_step) state = 2;",
        "@(initial_step) if (V(clk) > 0.45) state = 0;",
        "@(initial_step) state = 0; @(cross(V(clk) - 0.45, +1)) state = state + 1;",
    ],
)
def test_no_default_transition_case_requires_proven_selector_domain(
    state_write: str,
) -> None:
    src = f"""\
`include "disciplines.vams"
module bad_case_domain(clk, out);
    input clk;
    output out;
    electrical clk, out;
    integer state;
    analog begin
        {state_write}
        case (state)
            0: V(out) <+ transition(0.0, 0, 200p);
            1: V(out) <+ transition(0.9, 0, 200p);
        endcase
    end
endmodule
"""
    module = parse(src)
    with pytest.raises(ValueError, match="selector"):
        _validate_va_spectre_compat(module)
    with pytest.raises(CompilationError, match="selector"):
        compile_module(module)


def test_transition_case_requires_identical_output_sets() -> None:
    src = """\
`include "disciplines.vams"
module bad_case_outputs(out0, out1);
    output voltage out0, out1;
    integer state;
    analog begin
        @(initial_step) state = 0;
        case (state)
            0: V(out0) <+ transition(0.9, 0, 200p);
            1: V(out1) <+ transition(0.9, 0, 200p);
        endcase
    end
endmodule
"""
    with pytest.raises(CompilationError, match="identical output set"):
        compile_module(parse(src))
