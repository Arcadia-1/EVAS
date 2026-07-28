"""Round-2 regressions for timer rearm and event-to-output publication.

These tests intentionally keep timer deadline state separate from transition
target/publication state.  The exact frozen cells are documented in the E3
lane ledger; the models here are reduced mechanism discriminators.
"""

from pathlib import Path

import numpy as np
import pytest

from evas.compiler.parser import parse
from evas.netlist.runner import evas_simulate
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc


def _simulate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_name: str,
    model: str,
    deck: str,
):
    (tmp_path / f"{model_name}.va").write_text(model)
    scs_path = tmp_path / "tb.scs"
    scs_path.write_text(deck)
    monkeypatch.setenv("EVAS_ENGINE", "evas-rust")
    output_dir = tmp_path / "out"
    assert evas_simulate(str(scs_path), output_dir=str(output_dir))
    return np.genfromtxt(output_dir / "tran.csv", delimiter=",", names=True)


def _first_rise(time: np.ndarray, values: np.ndarray, threshold: float = 0.45) -> float:
    indices = np.flatnonzero((values[:-1] < threshold) & (values[1:] >= threshold)) + 1
    assert indices.size, "expected a published rising edge"
    return float(time[indices[0]])


def test_f326_periodic_timer_rearms_when_cross_moves_start_from_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F326 G0: accepted cross must replace the pending periodic start."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_rearm",
        model="""\
`include "disciplines.vams"
module periodic_rearm(trigger, accepted, fired);
  input trigger; output accepted, fired; electrical trigger, accepted, fired;
  real due; integer armed, accepted_state, fired_state;
  analog begin
    @(initial_step) begin due=1e99; armed=0; accepted_state=0; fired_state=0; end
    @(cross(V(trigger)-0.45,+1)) begin
      due=$abstime+1n; armed=1; accepted_state=1;
    end
    @(timer(due,1.0)) if (armed) begin fired_state=1; armed=0; end
    V(accepted) <+ accepted_state ? 0.9 : 0.0;
    V(fired) <+ transition(fired_state ? 0.9 : 0.0,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_rearm.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 4n 0.9]
XDUT (trigger accepted fired) periodic_rearm
tran tran stop=4n maxstep=25p
save trigger accepted fired
""",
    )

    assert data["accepted"][data["time"] >= 1.2e-9].min() > 0.8
    assert _first_rise(data["time"], data["fired"]) == pytest.approx(2.05e-9, abs=40e-12)


def test_periodic_timer_start_change_after_first_fire_keeps_pending_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A live start may arm the first deadline but must not retime later periods."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_grid_stability",
        model="""\
`include "disciplines.vams"
module periodic_grid_stability(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer count;
  analog begin
    @(initial_step) begin start=1n; count=0; end
    @(cross(V(trigger)-0.45,+1)) start=$abstime+0.5n;
    @(timer(start,1n)) count=count+1;
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_grid_stability.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 3n 0.9]
XDUT (trigger count_o) periodic_grid_stability
tran tran stop=3n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.4e-9)]
    assert before_grid.max() < 1.1
    assert after_grid.min() > 1.9


def test_periodic_timer_live_first_start_can_move_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Before the first accepted fire, the start expression remains live."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_first_start_moves_later",
        model="""\
`include "disciplines.vams"
module periodic_first_start_moves_later(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer count;
  analog begin
    @(initial_step) begin start=1n; count=0; end
    @(cross(V(trigger)-0.45,+1)) start=$abstime+1n;
    @(timer(start,1n)) count=count+1;
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_first_start_moves_later.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 0.4n 0 0.5n 0.9 2n 0.9]
XDUT (trigger count_o) periodic_first_start_moves_later
tran tran stop=2n maxstep=20p
save trigger count_o
""",
    )

    old_deadline = data["count_o"][(data["time"] >= 1.1e-9) & (data["time"] <= 1.3e-9)]
    moved_deadline = data["count_o"][(data["time"] >= 1.6e-9) & (data["time"] <= 1.8e-9)]
    assert old_deadline.max() < 0.1
    assert moved_deadline.min() > 0.9


def test_periodic_timer_due_first_deadline_is_not_postponed_same_timestep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A live start update must not move a first deadline that is already due."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_due_first_deadline_not_postponed",
        model="""\
`include "disciplines.vams"
module periodic_due_first_deadline_not_postponed(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer count;
  analog begin
    @(initial_step) begin start=1n; count=0; end
    @(cross(V(trigger)-0.45,+1)) start=$abstime+0.5n;
    @(timer(start,1n)) count=count+1;
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_due_first_deadline_not_postponed.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 0.9n 0 1.1n 0.9 1.8n 0.9]
XDUT (trigger count_o) periodic_due_first_deadline_not_postponed
tran tran stop=1.8n maxstep=20p
save trigger count_o
""",
    )

    already_due_window = data["count_o"][(data["time"] >= 1.15e-9) & (data["time"] <= 1.3e-9)]
    assert already_due_window.min() > 0.9


def test_periodic_guard_that_stays_true_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`if (armed) armed=1` is not a self-disarming one-shot deadline."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_guard_stays_true",
        model="""\
`include "disciplines.vams"
module periodic_guard_stays_true(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, count;
  analog begin
    @(initial_step) begin start=1n; armed=1; count=0; end
    @(cross(V(trigger)-0.45,+1)) start=$abstime+0.2n;
    @(timer(start,1n)) if (armed) begin count=count+1; armed=1; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_guard_stays_true.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_guard_stays_true
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_or_guard_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A compound positive guard is not a simple self-disarming one-shot."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_or_guard",
        model="""\
`include "disciplines.vams"
module periodic_or_guard(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, mode, count;
  analog begin
    @(initial_step) begin start=1n; armed=1; mode=0; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; armed=1; end
    @(timer(start,1n)) if (armed || mode) begin count=count+1; armed=0; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_or_guard.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_or_guard
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_negated_guard_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A negated guard is not a positive single-state self-disarm."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_negated_guard",
        model="""\
`include "disciplines.vams"
module periodic_negated_guard(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, count;
  analog begin
    @(initial_step) begin start=1n; armed=0; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; armed=0; end
    @(timer(start,1n)) if (!armed) begin count=count+1; armed=0; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_negated_guard.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_negated_guard
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_nested_guard_zero_assignment_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A nested conditional zero assignment is not immediate guard ownership."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_nested_guard_zero",
        model="""\
`include "disciplines.vams"
module periodic_nested_guard_zero(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, mode, count;
  analog begin
    @(initial_step) begin start=1n; armed=1; mode=1; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; armed=1; end
    @(timer(start,1n)) if (armed) begin count=count+1; if (mode) armed=0; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_nested_guard_zero.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_nested_guard_zero
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_nested_different_guard_zero_assignment_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A nested self-disarming guard is not the timer body's immediate guard."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_nested_different_guard_zero",
        model="""\
`include "disciplines.vams"
module periodic_nested_different_guard_zero(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, mode, count;
  analog begin
    @(initial_step) begin start=1n; armed=1; mode=1; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; armed=1; mode=1; end
    @(timer(start,1n)) if (armed) begin count=count+1; if (mode) mode=0; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_nested_different_guard_zero.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_nested_different_guard_zero
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_guard_rewritten_after_zero_does_not_rearm_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A guard that is set back to true is not finally self-disarmed."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_guard_rewritten_after_zero",
        model="""\
`include "disciplines.vams"
module periodic_guard_rewritten_after_zero(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer armed, active, count;
  analog begin
    @(initial_step) begin start=1n; armed=1; active=1; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; armed=1; active=1; end
    @(timer(start,1n)) if (armed && active) begin count=count+1; armed=0; armed=1; end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_guard_rewritten_after_zero.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_guard_rewritten_after_zero
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_periodic_sibling_top_level_if_does_not_define_timer_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A sibling top-level if is not the timer body's wrapper guard."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_sibling_top_level_if",
        model="""\
`include "disciplines.vams"
module periodic_sibling_top_level_if(trigger, count_o);
  input trigger; output count_o; electrical trigger, count_o;
  real start; integer mode, count;
  analog begin
    @(initial_step) begin start=1n; mode=1; count=0; end
    @(cross(V(trigger)-0.45,+1)) begin start=$abstime+0.2n; mode=1; end
    @(timer(start,1n)) begin
      if (mode) mode=0;
      count=count+1;
    end
    V(count_o) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_sibling_top_level_if.va"
Vtrigger (trigger 0) vsource type=pwl wave=[0 0 1.3n 0 1.4n 0.9 2.4n 0.9]
XDUT (trigger count_o) periodic_sibling_top_level_if
tran tran stop=2.4n maxstep=20p
save trigger count_o
""",
    )

    before_grid = data["count_o"][(data["time"] >= 1.7e-9) & (data["time"] <= 1.9e-9)]
    after_grid = data["count_o"][(data["time"] >= 2.1e-9) & (data["time"] <= 2.3e-9)]
    assert before_grid.max() < 1.2
    assert after_grid.min() > 1.9


def test_f326_periodic_timer_with_large_period_rearms_after_prior_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F326 exact shape: timer(start, 1.0) is reused as an armed deadline."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="periodic_large_period_rearm",
        model="""\
`include "disciplines.vams"
module periodic_large_period_rearm(clk_in, clk_out);
  input clk_in; output clk_out; electrical clk_in, clk_out;
  real rise_time, fall_time, clk_val;
  integer have_rise, have_fall, active;
  analog begin
    @(initial_step) begin
      rise_time=1e99; fall_time=1e99; clk_val=0; have_rise=0; have_fall=0; active=1;
    end
    @(cross(V(clk_in)-0.45,+1)) begin
      rise_time=$abstime+200p; have_rise=1;
    end
    @(timer(rise_time,1.0)) if (have_rise && active) begin
      clk_val=0.9; fall_time=$abstime+200p; have_fall=1; have_rise=0;
    end
    @(timer(fall_time,1.0)) if (have_fall) begin
      clk_val=0.0; have_fall=0;
    end
    V(clk_out) <+ transition(clk_val,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "periodic_large_period_rearm.va"
Vclk (clk_in 0) vsource type=pwl wave=[0 0 9n 0 10n 0.9 12n 0.9 12.2n 0 19n 0 20n 0.9 22n 0.9 22.2n 0 30n 0]
XDUT (clk_in clk_out) periodic_large_period_rearm
tran tran stop=30n maxstep=25p
save clk_in clk_out
""",
    )

    rises = np.flatnonzero((data["clk_out"][:-1] < 0.45) & (data["clk_out"][1:] >= 0.45)) + 1
    assert data["time"][rises].tolist() == pytest.approx([9.715e-9, 19.715e-9], abs=50e-12)


def test_combined_timer_body_does_not_rearm_zero_consumed_sibling_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Timer-or-timer shared bodies retain the combined-event ownership guard."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="combined_timer_ownership",
        model="""\
`include "disciplines.vams"
module combined_timer_ownership(count_o);
  output count_o; electrical count_o;
  real clear_at, kick_at; integer count;
  analog begin
    @(initial_step) begin clear_at=0; kick_at=1n; count=0; end
    @(timer(clear_at) or timer(kick_at)) begin
      if ($abstime>0.5n) begin
        count=count+1;
        clear_at=$abstime+1n;
        kick_at=1e99;
      end
    end
    V(count_o) <+ count;
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "combined_timer_ownership.va"
XDUT (count_o) combined_timer_ownership
tran tran stop=3n maxstep=20p
save count_o
""",
    )

    settled = data["count_o"][data["time"] >= 1.2e-9]
    assert settled.min() == pytest.approx(1.0, abs=1e-12)
    assert settled.max() == pytest.approx(1.0, abs=1e-12)


def test_combined_absolute_timer_member_uses_state_owned_due_path():
    """Combined timer/cross members keep the standalone state-owned timer path."""

    source = """\
`include "disciplines.vams"
module combined_absolute_rearm(ref, clk, fired_o);
  input ref; output clk, fired_o; electrical ref, clk, fired_o;
  real next_edge, half_period, sense; integer clk_state, fired;
  analog begin
    @(initial_step) begin next_edge=0.5n; half_period=0.5n; clk_state=0; fired=0; end
    sense = V(ref) - 0.45;
    @(timer(next_edge) or cross(sense,+1)) begin
      if ($abstime >= next_edge - 1p) begin
        clk_state = !clk_state;
        fired = fired + 1;
        next_edge = next_edge + half_period;
      end
    end
    V(clk) <+ transition(clk_state ? 0.9 : 0.0,0,10p,10p);
    V(fired_o) <+ fired;
  end
endmodule
"""
    Model = compile_module(parse(source))
    assert Model._state_owned_timer_targets == (("timer_0", "next_edge"),)

    model = Model()
    model.node_map = {"ref": "ref", "clk": "clk", "fired_o": "fired_o"}
    sim = Simulator()
    sim.add_source("ref", dc(0.0))
    sim.add_model(model)
    sim.record("fired_o")
    result = sim.run(tstop=3e-9, tstep=3e-9, max_step=3e-9)

    assert result.signals["fired_o"].max() == pytest.approx(6.0, abs=1e-12)
    assert model._perf_stats["timer_state_owned_checks"] > 0
    assert model._perf_stats["timer_state_owned_fires"] == 6


def test_post_update_combined_cross_body_republishes_absolute_timer_rearm():
    """Self-output combined cross bodies must publish timer rearms for future steps."""

    source = """\
`include "disciplines.vams"
module post_update_combined_rearm(clk, count_o);
  output clk, count_o; electrical clk, count_o;
  real next_edge; integer drive, count;
  analog begin
    @(initial_step) begin next_edge=2n; drive=0; count=0; end
    @(timer(1n)) drive = 1;
    @(timer(next_edge) or cross(V(clk)-0.45,+1)) begin
      count = count + 1;
      next_edge = $abstime + 0.5n;
    end
    V(clk) <+ drive ? 0.9 : 0.0;
    V(count_o) <+ count;
  end
endmodule
"""
    Model = compile_module(parse(source))
    assert Model._has_post_update_events is True
    assert Model._state_owned_timer_targets == (("timer_1", "next_edge"),)

    model = Model()
    model.node_map = {"clk": "clk", "count_o": "count_o"}
    sim = Simulator()
    sim.add_model(model)
    sim.record("count_o")
    result = sim.run(tstop=1.75e-9, tstep=250e-12, max_step=250e-12)

    assert result.signals["count_o"][-1] == pytest.approx(2.0, abs=1e-12)
    assert model._perf_stats["timer_state_owned_fires"] >= 1


def test_transition_target_positive_control_publishes_before_gated_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Positive control: bare target publication is distinct from deadline acceptance."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="gated_transition_target",
        model="""\
`include "disciplines.vams"
module gated_transition_target(clk_in, clk_out, accepted);
  input clk_in; output clk_out, accepted; electrical clk_in, clk_out, accepted;
  real due, target; integer pending, accepted_count;
  analog begin
    @(initial_step) begin due=1e99; target=0; pending=0; accepted_count=0; end
    @(cross(V(clk_in)-0.45,+1)) begin
      target=0; due=$abstime+1n; pending=1; accepted_count=accepted_count+1;
    end
    if (pending) begin
      $bound_step(due-$abstime);
      if ($abstime>=due) begin target=0.9; pending=0; end
    end
    V(clk_out) <+ transition(target,0,20p,20p);
    V(accepted) <+ accepted_count;
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "gated_transition_target.va"
Vclk (clk_in 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 2.5n 0.9 2.6n 0 3.5n 0 3.6n 0.9 6n 0.9]
XDUT (clk_in clk_out accepted) gated_transition_target
tran tran stop=6n maxstep=200p
save clk_in clk_out accepted
""",
    )

    assert data["accepted"][data["time"] >= 3.7e-9].min() > 1.9
    low_window = data["clk_out"][(data["time"] >= 3.7e-9) & (data["time"] <= 4.4e-9)]
    high_window = data["clk_out"][data["time"] >= 4.7e-9]
    assert low_window.max() < 0.1
    assert high_window.min() > 0.8


def test_back_to_back_delayed_transition_preserves_short_low_pulse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A delayed transition retarget must not swallow a short queued pulse."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="delayed_transition_pulse",
        model="""\
`include "disciplines.vams"
module delayed_transition_pulse(clk_in, clk_out, accepted);
  input clk_in; output clk_out, accepted; electrical clk_in, clk_out, accepted;
  real due, target; integer pending, accepted_count;
  analog begin
    @(initial_step) begin due=1e99; target=0.9; pending=0; accepted_count=0; end
    @(cross(V(clk_in)-0.45,+1)) begin
      target=0.0; due=$abstime+200p; pending=1; accepted_count=accepted_count+1;
    end
    if (pending) begin
      $bound_step(due-$abstime);
      if ($abstime>=due) begin target=0.9; pending=0; end
    end
    V(clk_out) <+ transition(target,200p,200p);
    V(accepted) <+ accepted_count;
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "delayed_transition_pulse.va"
Vclk (clk_in 0) vsource type=pwl wave=[0 0 9n 0 10n 0.9 12n 0.9 12.2n 0 20n 0.9 22n 0.9]
XDUT (clk_in clk_out accepted) delayed_transition_pulse
tran tran stop=24n maxstep=50p
save clk_in clk_out accepted
""",
    )

    assert data["accepted"][data["time"] >= 16.2e-9].min() > 1.9
    second_pulse = data["clk_out"][(data["time"] >= 16.3e-9) & (data["time"] <= 16.8e-9)]
    assert second_pulse.min() < 0.1
    rises = np.flatnonzero((data["clk_out"][:-1] < 0.45) & (data["clk_out"][1:] >= 0.45)) + 1
    assert len(data["time"][rises]) >= 2


def test_absolute_timer_positive_control_rearms_from_cross_and_then_from_its_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Positive control: bare absolute-timer start and body rearm already work."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="absolute_rearm",
        model="""\
`include "disciplines.vams"
module absolute_rearm(enable, accepted, clk);
  input enable; output accepted, clk; electrical enable, accepted, clk;
  real next_edge; integer running, accepted_state, clk_state;
  analog begin
    @(initial_step) begin next_edge=1e99; running=0; accepted_state=0; clk_state=0; end
    @(cross(V(enable)-0.45,+1)) begin
      running=1; accepted_state=1; next_edge=$abstime+1n;
    end
    @(timer(next_edge)) if (running) begin
      clk_state=!clk_state; next_edge=$abstime+1n;
    end
    V(accepted) <+ accepted_state ? 0.9 : 0.0;
    V(clk) <+ transition(clk_state ? 0.9 : 0.0,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "absolute_rearm.va"
Venable (enable 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 5n 0.9]
XDUT (enable accepted clk) absolute_rearm
tran tran stop=5n maxstep=25p
save enable accepted clk
""",
    )

    assert data["accepted"][data["time"] >= 1.2e-9].min() > 0.8
    rises = np.flatnonzero((data["clk"][:-1] < 0.45) & (data["clk"][1:] >= 0.45)) + 1
    assert data["time"][rises].tolist() == pytest.approx([2.05e-9, 4.05e-9], abs=40e-12)


def test_f362_cross_start_survives_continuous_stop_gate_and_fires_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F362: distinguish accepted start state from later timer rearm state."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="gated_absolute_rearm",
        model="""\
`include "disciplines.vams"
module gated_absolute_rearm(enable, rst, running_o, clk);
  input enable, rst; output running_o, clk; electrical enable, rst, running_o, clk;
  real next_edge; integer running, clk_state;
  analog begin
    @(initial_step) begin next_edge=1e99; running=0; clk_state=0; end
    @(cross(V(enable)-0.45,+1)) begin
      if (V(rst)<=0.45) begin running=1; next_edge=$abstime+1n; end
    end
    if (!(V(enable)>0.45 && V(rst)<=0.45)) begin
      running=0; clk_state=0; next_edge=1e99;
    end
    @(timer(next_edge)) if (running) begin
      clk_state=!clk_state; next_edge=$abstime+1n;
    end
    V(running_o) <+ running ? 0.9 : 0.0;
    V(clk) <+ transition(clk_state ? 0.9 : 0.0,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "gated_absolute_rearm.va"
Venable (enable 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 5n 0.9]
Vrst (rst 0) vsource dc=0
XDUT (enable rst running_o clk) gated_absolute_rearm
tran tran stop=5n maxstep=25p
save enable rst running_o clk
""",
    )

    assert data["running_o"][data["time"] >= 1.2e-9].min() > 0.8
    rises = np.flatnonzero((data["clk"][:-1] < 0.45) & (data["clk"][1:] >= 0.45)) + 1
    assert data["time"][rises].tolist() == pytest.approx([2.05e-9, 4.05e-9], abs=40e-12)


def test_f168_combined_input_cross_publishes_first_settled_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F168: accepted input event must publish without waiting for another event."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="combined_publication",
        model="""\
`include "disciplines.vams"
module combined_publication(din0, din1, din2, din3, dout);
  input din0, din1, din2, din3; output dout;
  electrical din0, din1, din2, din3, dout; real level;
  analog begin
    @(initial_step or cross(V(din0)-0.45,0) or cross(V(din1)-0.45,0) or
      cross(V(din2)-0.45,0) or cross(V(din3)-0.45,0)) begin
      level=0.2+(V(din0)>0.45 ? 0.1 : 0.0)+(V(din1)>0.45 ? 0.2 : 0.0)
        +(V(din2)>0.45 ? 0.4 : 0.0)+(V(din3)>0.45 ? 0.8 : 0.0);
    end
    V(dout) <+ level;
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "combined_publication.va"
Vdin0 (din0 0) vsource type=pwl wave=[0 0 0.999n 0 1n 1 3n 1]
Vdin1 (din1 0) vsource dc=0
Vdin2 (din2 0) vsource dc=0
Vdin3 (din3 0) vsource dc=0
XDUT (din0 din1 din2 din3 dout) combined_publication
tran tran stop=3n maxstep=500p
save din0 din1 din2 din3 dout
""",
    )

    pre = data["dout"][(data["time"] >= 0.2e-9) & (data["time"] <= 0.9e-9)]
    settled = data["dout"][data["time"] >= 1.2e-9]
    assert pre.min() == pytest.approx(0.2, abs=1e-12)
    assert settled.min() == pytest.approx(0.3, abs=1e-12)


def test_f178_initial_and_first_cross_publish_transition_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F178 positive control: initial target and first transitioned update settle."""

    data = _simulate(
        tmp_path,
        monkeypatch,
        model_name="initial_transition",
        model="""\
`include "disciplines.vams"
module initial_transition(clk, out);
  input clk; output out; electrical clk, out; integer count;
  analog begin
    @(initial_step) count=0;
    @(cross(V(clk)-0.45,+1)) count=count+1;
    V(out) <+ transition(count,0,20p,20p);
  end
endmodule
""",
        deck="""\
simulator lang=spectre
global 0
ahdl_include "initial_transition.va"
Vclk (clk 0) vsource type=pwl wave=[0 0 1n 0 1.1n 0.9 3n 0.9]
XDUT (clk out) initial_transition
tran tran stop=3n maxstep=25p
save clk out
""",
    )

    initial = data["out"][(data["time"] >= 0.2e-9) & (data["time"] <= 0.9e-9)]
    settled = data["out"][data["time"] >= 1.2e-9]
    assert np.max(np.abs(initial)) < 1e-12
    assert settled.min() > 0.99
