"""Regressions for clock-edge discrete-state publication on the Rust path."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc, pwl


RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


@pytest.fixture(scope="module", autouse=True)
def _rust_core() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def _run(source: str, sources: dict[str, object], records: tuple[str, ...], tstop: float):
    model = compile_module(parse(source))()
    model.node_map = {name.lower(): name for name in (*sources, *records)}
    sim = Simulator()
    for name, stimulus in sources.items():
        sim.add_source(name, stimulus)
    sim.add_model(model)
    for name in records:
        sim.record(name)
    result = sim.run(
        tstop=tstop,
        tstep=250e-12,
        max_step=250e-12,
        record_step=10e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )
    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert sim._perf_stats["rust_full_model_required_correctness_fallbacks"] == 0
    return model, result


def _sample(result, signal: str, time: float) -> float:
    index = min(range(len(result.time)), key=lambda idx: abs(result.time[idx] - time))
    assert abs(result.time[index] - time) <= 1e-15
    return float(result.signals[signal][index])


def test_clock_body_sequencing_publishes_the_previous_sample() -> None:
    """F143/F151: ordered assignments commit before output publication."""

    source = """\
`include "disciplines.vams"
module delayed_sample(clk, din, out);
  input voltage clk, din; output voltage out;
  real previous, sampled_current, published;
  analog begin
    @(initial_step) begin previous=0; sampled_current=0; published=0; end
    @(cross(V(clk)-0.45,+1)) begin
      published=previous;
      sampled_current=V(din);
      previous=sampled_current;
    end
    V(out)<+transition(published,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {
            "CLK": pwl(
                [0, 0.9e-9, 1.1e-9, 1.9e-9, 2.1e-9, 3e-9],
                [0, 0, 0.9, 0.9, 0, 0],
            ),
            "DIN": dc(0.9),
        },
        ("OUT",),
        3e-9,
    )

    assert model.state["previous"] == pytest.approx(0.9)
    assert model.state["sampled_current"] == pytest.approx(0.9)
    assert model.state["published"] == pytest.approx(0.0)
    assert _sample(result, "OUT", 1.2e-9) == pytest.approx(0.0, abs=1e-12)


def test_event_record_distinguishes_direct_and_transitioned_publication() -> None:
    """Clock state is visible immediately; transition settling remains scheduled."""

    source = """\
`include "disciplines.vams"
module publication_order(clk, direct_out, smooth_out);
  input voltage clk; output voltage direct_out, smooth_out; integer state;
  analog begin
    @(initial_step) state=0;
    @(cross(V(clk)-0.45,+1)) state=1;
    V(direct_out)<+state ? 0.9 : 0.0;
    V(smooth_out)<+transition(state ? 0.9 : 0.0,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {"CLK": pwl([0, 0.9e-9, 1.1e-9, 2e-9], [0, 0, 0.9, 0.9])},
        ("DIRECT_OUT", "SMOOTH_OUT"),
        2e-9,
    )

    assert model.state["state"] == pytest.approx(1.0)
    assert _sample(result, "DIRECT_OUT", 1.0e-9) == pytest.approx(0.9)
    assert _sample(result, "SMOOTH_OUT", 1.0e-9) == pytest.approx(0.0, abs=1e-7)
    assert _sample(result, "SMOOTH_OUT", 1.02e-9) == pytest.approx(0.9, abs=1e-7)


def test_continuous_clock_detector_refreshes_state_chain_once() -> None:
    """F342: a clock edge detected in the continuous body refreshes outputs."""

    source = """\
`include "disciplines.vams"
module continuous_clock(clk, out);
  input voltage clk; output voltage out; integer previous_clock, count; real level;
  analog begin
    @(initial_step) begin previous_clock=0; count=0; level=0; end
    if (V(clk)>0.45 && previous_clock==0) begin
      count=count+1;
      level=0.3*count;
    end
    previous_clock=V(clk)>0.45;
    V(out)<+transition(level,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {
            "CLK": pwl(
                [0, 0.9e-9, 1.1e-9, 1.9e-9, 2.1e-9, 2.9e-9, 3.1e-9, 4e-9],
                [0, 0, 0.9, 0.9, 0, 0, 0.9, 0.9],
            )
        },
        ("OUT",),
        4e-9,
    )

    assert model.state["count"] == pytest.approx(2.0)
    assert model.state["level"] == pytest.approx(0.6)
    assert _sample(result, "OUT", 3.2e-9) == pytest.approx(0.6, abs=1e-9)


def test_f305_clock_sample_state_matches_first_settled_output() -> None:
    """F305-shaped sampling commits its target before transition settling."""

    source = """\
`include "disciplines.vams"
module sampled_hold(clk, vin, out);
  input voltage clk, vin; output voltage out; real held; integer samples;
  analog begin
    @(initial_step) begin held=0.45; samples=0; end
    @(cross(V(clk)-0.45,+1)) begin held=V(vin); samples=samples+1; end
    V(out)<+transition(held,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {
            "CLK": pwl([0, 0.9e-9, 1.1e-9, 1.9e-9, 2.1e-9, 3e-9], [0, 0, 0.9, 0.9, 0, 0]),
            "VIN": dc(0.7),
        },
        ("OUT",),
        3e-9,
    )

    assert model.state["samples"] == pytest.approx(1.0)
    assert model.state["held"] == pytest.approx(0.7)
    assert _sample(result, "OUT", 1.2e-9) == pytest.approx(0.7, abs=1e-9)


def test_f336_periodic_sampler_state_matches_first_settled_output() -> None:
    """F336-shaped static timer publishes the sampled envelope state."""

    source = """\
`include "disciplines.vams"
module timer_sample(clk, out);
  input voltage clk; output voltage out; real previous, level; integer samples;
  analog begin
    @(initial_step) begin previous=0; level=0; samples=0; end
    @(timer(0,250p)) begin
      if (V(clk)>0.45 && previous<=0.45) begin level=level+0.2; samples=samples+1; end
      previous=V(clk);
    end
    V(out)<+transition(level,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {"CLK": pwl([0, 0.9e-9, 1.0e-9, 1.4e-9, 1.5e-9, 2e-9], [0, 0, 0.9, 0.9, 0, 0])},
        ("OUT",),
        2e-9,
    )

    assert model.state["samples"] == pytest.approx(1.0)
    assert model.state["level"] == pytest.approx(0.2)
    assert _sample(result, "OUT", 1.27e-9) == pytest.approx(0.2, abs=1e-9)


def test_f390_combined_control_event_updates_trim_once() -> None:
    """F390-shaped combined control event publishes one trim increment."""

    source = """\
`include "disciplines.vams"
module trim_once(clk, rst, enable, trim);
  input voltage clk, rst, enable; output voltage trim; integer code;
  analog begin
    @(initial_step) code=0;
    @(cross(V(clk)-0.45,+1) or cross(V(rst)-0.45,+1) or cross(V(enable)-0.45,-1)) begin
      if (V(rst)>0.45 || V(enable)<=0.45) code=0; else code=code+1;
    end
    V(trim)<+transition(code,0,20p,20p);
  end
endmodule
"""
    model, result = _run(
        source,
        {
            "CLK": pwl([0, 0.9e-9, 1.1e-9, 2e-9], [0, 0, 0.9, 0.9]),
            "RST": dc(0),
            "ENABLE": dc(0.9),
        },
        ("TRIM",),
        2e-9,
    )

    assert model.state["code"] == pytest.approx(1.0)
    assert _sample(result, "TRIM", 1.2e-9) == pytest.approx(1.0, abs=1e-9)
