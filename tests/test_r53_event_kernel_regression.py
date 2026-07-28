"""Focused regressions for event-state publication and hierarchical event state."""

from pathlib import Path

import numpy as np
import pytest

from evas.compiler.parser import parse
from evas.netlist.runner import evas_simulate
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc, pwl


def _window(result, signal: str, start: float, stop: float):
    return result.signals[signal][(result.time >= start) & (result.time <= stop)]


def test_event_state_is_published_through_transition_to_downstream_latch():
    """F346: event state must reach a downstream level-sensitive latch."""

    sources = (
        """\
`include "disciplines.vams"
module edge_latch(start, clear_i, start_evt);
  input voltage start, clear_i; output voltage start_evt; integer detected;
  analog begin
    @(initial_step) detected = 0;
    @(cross(V(start)-0.45, +1)) detected = 1;
    if (V(clear_i) >= 0.45) detected = 0;
    V(start_evt) <+ transition(detected ? 0.9 : 0.0, 0, 200p);
  end
endmodule
""",
        """\
`include "disciplines.vams"
module counter(clk, start_evt, valid_i, clear_o);
  input voltage clk, start_evt; output voltage valid_i, clear_o;
  integer count, armed, valid_state, clear_state;
  analog begin
    @(initial_step) begin count=0; armed=0; valid_state=0; clear_state=0; end
    @(cross(V(start_evt)-0.45, +1)) begin
      count=0; armed=1; valid_state=0; clear_state=1;
    end
    @(cross(V(clk)-0.45, +1)) if (armed) begin
      count=count+1; if (count > 3) begin armed=0; valid_state=1; end
    end
    if (V(start_evt) < 0.45) clear_state=0;
    V(valid_i) <+ transition(valid_state ? 0.9 : 0.0, 0, 200p);
    V(clear_o) <+ transition(clear_state ? 0.9 : 0.0, 0, 200p);
  end
endmodule
""",
        """\
`include "disciplines.vams"
module status_latch(start_evt, valid_i, valid);
  input voltage start_evt, valid_i; output voltage valid; integer latched;
  analog begin
    @(initial_step) latched=0;
    if (V(start_evt) >= 0.45) latched=0; else if (V(valid_i) >= 0.45) latched=1;
    V(valid) <+ transition(latched ? 0.9 : 0.0, 0, 200p);
  end
endmodule
""",
    )
    edge, counter, latch = (compile_module(parse(source))() for source in sources)
    edge.node_map = {"start": "start", "clear_i": "clear", "start_evt": "start_evt"}
    counter.node_map = {
        "clk": "clk", "start_evt": "start_evt", "valid_i": "valid_i", "clear_o": "clear"
    }
    latch.node_map = {"start_evt": "start_evt", "valid_i": "valid_i", "valid": "valid"}

    sim = Simulator()
    sim.add_source("start", pwl([0, 0.4e-9, 0.6e-9, 0.8e-9, 1e-9, 6e-9], [0, 0, 0.9, 0.9, 0, 0]))
    sim.add_source(
        "clk",
        pwl(
            [0, 1.4e-9, 1.6e-9, 2.4e-9, 2.6e-9, 3.4e-9, 3.6e-9, 4.4e-9, 4.6e-9, 6e-9],
            [0, 0, 0.9, 0, 0.9, 0, 0.9, 0, 0.9, 0.9],
        ),
    )
    for model in (edge, counter, latch):
        sim.add_model(model)
    for signal in ("start_evt", "clear", "valid_i", "valid"):
        sim.record(signal)
    result = sim.run(
        tstop=6e-9,
        tstep=500e-12,
        record_step=100e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert result.signals["start_evt"].max() > 0.8
    assert result.signals["clear"].max() > 0.8
    assert _window(result, "valid_i", 5e-9, 6e-9).min() > 0.8
    assert _window(result, "valid", 5.2e-9, 6e-9).min() > 0.8


def test_hierarchical_event_state_counts_each_feedback_edge_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F351/1351: hierarchy must not replay the first accepted edge."""

    (tmp_path / "divider.va").write_text(
        """\
`include "disciplines.vams"
module divider(fb_clk, rst, enable, div2_clk);
  input fb_clk, rst, enable; output div2_clk;
  electrical fb_clk, rst, enable, div2_clk; integer edge_count, div_state;
  analog begin
    @(initial_step) begin edge_count=0; div_state=0; end
    @(cross(V(rst)-0.45,+1) or cross(V(enable)-0.45,-1)) begin edge_count=0; div_state=0; end
    @(cross(V(fb_clk)-0.45,+1)) begin
      if (V(rst)>0.45 || V(enable)<=0.45) begin edge_count=0; div_state=0; end
      else begin edge_count=edge_count+1; if (edge_count>=2) begin edge_count=0; div_state=1-div_state; end end
    end
    V(div2_clk) <+ transition(div_state ? 0.9 : 0.0, 0, 200p, 200p);
  end
endmodule
"""
    )
    (tmp_path / "top.va").write_text(
        """\
`include "disciplines.vams"
module divider_top(fb_clk,rst,enable,div2_clk);
  input fb_clk,rst,enable; output div2_clk; electrical fb_clk,rst,enable,div2_clk;
  divider u_divider(fb_clk,rst,enable,div2_clk);
endmodule
"""
    )
    (tmp_path / "tb.scs").write_text(
        """\
simulator lang=spectre
global 0
ahdl_include "top.va"
ahdl_include "divider.va"
Vrst (rst 0) vsource type=pwl wave=[0 0.9 3n 0.9 3.1n 0 18n 0]
Venable (enable 0) vsource type=pwl wave=[0 0 4n 0 4.1n 0.9 18n 0.9]
Vfb (fb_clk 0) vsource type=pwl wave=[0 0 5.6n 0 5.65n 0.9 8.15n 0.9 8.2n 0 14.3n 0 14.35n 0.9 16.85n 0.9 16.9n 0 18n 0]
XDUT (fb_clk rst enable div2_clk) divider_top
tran tran stop=18n maxstep=100p
save div2_clk
"""
    )
    monkeypatch.setenv("EVAS_ENGINE", "evas-rust")
    output_dir = tmp_path / "out"
    assert evas_simulate(str(tmp_path / "tb.scs"), output_dir=str(output_dir))
    data = np.genfromtxt(output_dir / "tran.csv", delimiter=",", names=True)

    first_edge = data["div2_clk"][(data["time"] >= 6.2e-9) & (data["time"] <= 13.8e-9)]
    second_edge = data["div2_clk"][(data["time"] >= 14.8e-9) & (data["time"] <= 16.7e-9)]
    assert first_edge.max() < 0.1
    assert second_edge.min() > 0.8


def test_separate_cross_body_rearms_absolute_timer_consumed_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A sibling cross may schedule a timer whose initial target fired at zero."""

    (tmp_path / "separate_timer_rearm.va").write_text(
        """\
`include "disciplines.vams"
module separate_timer_rearm(trigger, pulse);
  input trigger; output pulse; electrical trigger, pulse;
  real clear_at; integer pulse_state;
  analog begin
    @(initial_step) begin clear_at=0; pulse_state=0; end
    @(timer(clear_at)) pulse_state=0;
    @(cross(V(trigger)-0.45,+1)) begin
      pulse_state=1;
      clear_at=$abstime+1n;
    end
    V(pulse) <+ transition(pulse_state ? 0.9 : 0.0, 0, 20p, 20p);
  end
endmodule
"""
    )
    (tmp_path / "tb.scs").write_text(
        """\
simulator lang=spectre
global 0
ahdl_include "separate_timer_rearm.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 4n 0.9]
XDUT (trigger pulse) separate_timer_rearm
tran tran stop=4n maxstep=20p
save trigger pulse
"""
    )
    monkeypatch.setenv("EVAS_ENGINE", "evas-rust")
    output_dir = tmp_path / "out"
    assert evas_simulate(str(tmp_path / "tb.scs"), output_dir=str(output_dir))
    data = np.genfromtxt(output_dir / "tran.csv", delimiter=",", names=True)

    asserted = data["pulse"][(data["time"] >= 1.3e-9) & (data["time"] <= 1.9e-9)]
    cleared = data["pulse"][(data["time"] >= 2.3e-9) & (data["time"] <= 3.8e-9)]
    assert asserted.min() > 0.8
    assert cleared.max() < 0.1


def test_combined_cross_body_classifies_exact_touch_falling_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A shared +1/-1 cross body must read the fired child's post-cross side."""

    (tmp_path / "support_hysteretic_comparator.va").write_text(
        """\
`include "disciplines.vams"
module support_hysteretic_comparator(vdd, vss, inp, inm, out);
input vdd, vss, inp, inm;
output out;
electrical vdd, vss, inp, inm, out;
parameter real offset = 0.0;
parameter real vhys = 50m;
parameter real td = 120p;
parameter real tr = 30p;
real upper_th;
real lower_th;
integer state;
analog begin
    upper_th = offset + 0.5 * vhys;
    lower_th = offset - 0.5 * vhys;
    @(initial_step) begin
        state = (V(inp, inm) >= upper_th);
    end
    @(cross(V(inp, inm) - upper_th, +1)) begin
        state = 1;
    end
    @(cross(V(inp, inm) - lower_th, -1)) begin
        state = 0;
    end
    V(out, vss) <+ transition(state ? 1.0 : 0.0, td, tr, tr) * V(vdd, vss);
end
endmodule
"""
    )
    (tmp_path / "hysteresis_trip_characterizer.va").write_text(
        """\
`include "disciplines.vams"
module hysteresis_trip_characterizer(vdd, vss, vin, cmp_out, trip_rise, trip_fall, hyst_width, valid);
input vdd, vss, vin, cmp_out;
output trip_rise, trip_fall, hyst_width, valid;
electrical vdd, vss, vin, cmp_out, trip_rise, trip_fall, hyst_width, valid;
parameter real tr = 20p;
real trip_rise_val, trip_fall_val;
integer has_rise, has_fall;
analog begin
    @(initial_step) begin
        trip_rise_val = 0.0;
        trip_fall_val = 0.0;
        has_rise = 0;
        has_fall = 0;
    end
    @(cross(V(cmp_out, vss) - 0.5 * V(vdd, vss), +1) or
      cross(V(cmp_out, vss) - 0.5 * V(vdd, vss), -1)) begin
        if (V(cmp_out, vss) >= 0.5 * V(vdd, vss)) begin
            trip_rise_val = V(vin, vss);
            has_rise = 1;
        end else begin
            trip_fall_val = V(vin, vss);
            has_fall = 1;
        end
    end
    V(trip_rise, vss) <+ transition(trip_rise_val, 0.0, tr, tr);
    V(trip_fall, vss) <+ transition(trip_fall_val, 0.0, tr, tr);
    V(hyst_width, vss) <+ transition(trip_rise_val - trip_fall_val, 0.0, tr, tr);
    V(valid, vss) <+ transition((has_rise && has_fall) ? V(vdd, vss) : 0.0, 0.0, tr, tr);
end
endmodule
"""
    )
    (tmp_path / "tb.scs").write_text(
        """\
simulator lang=spectre
global 0
ahdl_include "hysteresis_trip_characterizer.va"
ahdl_include "support_hysteretic_comparator.va"
Vvdd (vdd 0) vsource dc=0.9
Vvss (vss 0) vsource dc=0.0
Vref (vref 0) vsource dc=0.45
Vvin (vin 0) vsource type=pwl wave=[0 0.420 10n 0.490 20n 0.420 30n 0.500 40n 0.410 44n 0.410]
XCMP (vdd vss vin vref cmp_out) support_hysteretic_comparator offset=5m vhys=50m td=120p tr=30p
XMEAS (vdd vss vin cmp_out trip_rise trip_fall hyst_width valid) hysteresis_trip_characterizer
tran tran stop=44n maxstep=20p
save vdd vss vin cmp_out trip_rise trip_fall hyst_width valid
"""
    )
    monkeypatch.setenv("EVAS_ENGINE", "evas-rust")
    output_dir = tmp_path / "out"
    assert evas_simulate(str(tmp_path / "tb.scs"), output_dir=str(output_dir))
    data = np.genfromtxt(output_dir / "tran.csv", delimiter=",", names=True)

    assert float(data["valid"][-1]) > 0.8
    assert float(data["trip_rise"][-1]) == pytest.approx(0.481, abs=0.004)
    assert float(data["trip_fall"][-1]) == pytest.approx(0.429, abs=0.004)
    assert float(data["hyst_width"][-1]) == pytest.approx(0.052, abs=0.004)
