from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator


RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


def _build_rust_core_or_skip() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def test_dense_periodic_timer_grows_record_capacity_until_complete() -> None:
    _build_rust_core_or_skip()
    source = """\
`include "disciplines.vams"
module dense_timer(out);
    output voltage out;
    integer state = 0;
    analog begin
        @(timer(0, 5f)) begin
            if (state == 0) begin
                state = 1;
                @(timer(20p, 5f))
                    state = 2;
            end
        end
        V(out) <+ transition(state, 0, 1p);
    end
endmodule
"""
    model = compile_module(parse(source))()
    model.node_map = {"out": "OUT"}
    sim = Simulator()
    sim.add_model(model)
    sim.record("OUT")

    result = sim.run(
        tstop=40e-12,
        tstep=5e-12,
        max_step=5e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert len(result.time) >= 8_000
    assert sim._perf_stats["rust_sim_program_runtime_attempts"] > 6
