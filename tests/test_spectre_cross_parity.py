"""Spectre parity regressions for cross event body sampling."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc, pwl

RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


def _build_rust_core_or_skip() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def test_bidirectional_cross_body_sees_post_side_for_transition_output() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module comparator(vdd, vss, vinp, vinn, out);
    input voltage vdd, vss, vinp, vinn;
    output voltage out;
    real out_state;

    analog begin
        @(initial_step) begin
            if (V(vinp, vss) > V(vinn, vss))
                out_state = 1.0;
            else
                out_state = 0.0;
        end

        @(cross(V(vinp, vss) - V(vinn, vss), 0, 1e-12)) begin
            if (V(vinp, vss) > V(vinn, vss))
                out_state = 1.0;
            else
                out_state = 0.0;
        end

        V(out) <+ transition(out_state * V(vdd, vss) + (1.0 - out_state) * V(vss), 0, 100p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {
        "vdd": "vdd",
        "vss": "vss",
        "vinp": "vinp",
        "vinn": "vinn",
        "out": "out",
    }

    sim = Simulator()
    sim.add_source("vdd", dc(0.9))
    sim.add_source("vss", dc(0.0))
    sim.add_source("vinn", dc(0.45))
    sim.add_source("vinp", pwl([0.0, 10e-9, 20e-9, 30e-9], [0.2, 0.7, 0.7, 0.2]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=30e-9,
        tstep=5e-9,
        record_step=1e-9,
        max_step=5e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert sim._perf_stats["rust_sim_program_event_fires"] >= 2
    assert result.signals["out"].max() == pytest.approx(0.9, abs=0.05)
    assert result.signals["out"][-1] == pytest.approx(0.0, abs=0.05)


def test_terminal_threshold_touch_does_not_force_post_side_on_rust_path() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module exact_touch_brownout(vin, out);
    input voltage vin;
    output voltage out;
    real target;

    analog begin
        @(initial_step) begin
            target = 1.0;
        end

        @(cross(V(vin) - 0.5, -1)) begin
            if (V(vin) < 0.5)
                target = 0.0;
            else
                target = 1.0;
        end

        V(out) <+ target;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source("vin", pwl([0.0, 10e-9], [1.0, 0.5]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=10e-9,
        tstep=10e-9,
        record_step=1e-9,
        max_step=10e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["out"][-1] == pytest.approx(1.0, abs=1e-12)


def test_negative_cross_terminal_touchdown_does_not_fire() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module terminal_touchdown(vin, out);
    input voltage vin;
    output voltage out;
    integer hit;

    analog begin
        @(initial_step)
            hit = 0;

        @(cross(V(vin) - 0.5, -1))
            hit = 1;

        V(out) <+ hit;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source("vin", pwl([0.0, 10e-9], [1.0, 0.5]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=10e-9,
        tstep=10e-9,
        record_step=1e-9,
        max_step=10e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["out"][-1] == pytest.approx(0.0, abs=1e-12)


def test_negative_cross_fires_after_zero_plateau_from_positive_side() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module zero_plateau_falling_cross(vin, out);
    input voltage vin;
    output voltage out;
    integer hit;

    analog begin
        @(initial_step)
            hit = 0;

        @(cross(V(vin) - 0.5, -1))
            hit = 1;

        V(out) <+ hit;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source("vin", pwl([0.0, 10e-9, 20e-9], [0.6, 0.5, 0.4]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=20e-9,
        tstep=10e-9,
        record_step=1e-9,
        max_step=10e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["out"][-1] == pytest.approx(1.0, abs=1e-12)


def test_window_cross_bodies_see_inside_region_on_both_slopes() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module window_comparator_ref(vdd, vss, vin, out);
    input voltage vdd, vss, vin;
    output voltage out;
    integer in_window;

    analog begin
        @(initial_step) begin
            if (V(vin, vss) > 0.3 && V(vin, vss) < 0.6)
                in_window = 1;
            else
                in_window = 0;
        end

        @(cross(V(vin, vss) - 0.3, 0)) begin
            if (V(vin, vss) > 0.3 && V(vin, vss) < 0.6)
                in_window = 1;
            else
                in_window = 0;
        end

        @(cross(V(vin, vss) - 0.6, 0)) begin
            if (V(vin, vss) > 0.3 && V(vin, vss) < 0.6)
                in_window = 1;
            else
                in_window = 0;
        end

        V(out) <+ transition(in_window ? V(vdd, vss) : 0.0, 0, 200p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vdd": "vdd", "vss": "vss", "vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source("vdd", dc(0.9))
    sim.add_source("vss", dc(0.0))
    sim.add_source("vin", pwl([0.0, 90e-9, 180e-9], [0.0, 0.9, 0.0]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=180e-9,
        tstep=10e-9,
        record_step=1e-9,
        max_step=10e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    times = result.time
    out = result.signals["out"]
    rising_inside = out[(times > 31e-9) & (times < 59e-9)].max()
    falling_inside = out[(times > 121e-9) & (times < 149e-9)].max()

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert rising_inside == pytest.approx(0.9, abs=0.05)
    assert falling_inside == pytest.approx(0.9, abs=0.05)


def test_dynamic_absolute_deadline_cross_does_not_arm_from_state_jump() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module dynamic_deadline_cross(clk, late, pulse_out);
    input voltage clk;
    output voltage late, pulse_out;
    real release_deadline;
    real pulse_deadline;
    integer late_state;
    integer pulse_state;

    analog begin
        @(initial_step) begin
            release_deadline = -1.0;
            pulse_deadline = -1.0;
            late_state = 0;
            pulse_state = 0;
        end

        @(cross(V(clk) - 0.5, +1)) begin
            release_deadline = $abstime + 1n;
            pulse_deadline = $abstime + 1n;
            pulse_state = 1;
        end

        @(cross($abstime - release_deadline, +1))
            late_state = 1;

        @(cross(pulse_deadline - $abstime, -1))
            pulse_state = 0;

        V(late) <+ late_state;
        V(pulse_out) <+ pulse_state;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"clk": "clk", "late": "late", "pulse_out": "pulse_out"}

    sim = Simulator()
    sim.add_source("clk", pwl([0.0, 1e-9, 4e-9], [0.0, 1.0, 1.0]))
    sim.add_model(model)
    sim.record("late")
    sim.record("pulse_out")

    result = sim.run(
        tstop=4e-9,
        tstep=500e-12,
        record_step=250e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["late"][-1] == pytest.approx(0.0, abs=1e-12)
    assert result.signals["pulse_out"][-1] == pytest.approx(1.0, abs=1e-12)


def test_retriggered_cross_reschedules_standalone_dynamic_timer() -> None:
    """Family 108: a separate cross update extends the public pulse."""

    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"
module retriggerable_pulse(sigin, sigout);
    input voltage sigin;
    output voltage sigout;
    real level;
    real pulse_end;
    analog begin
        @(initial_step) begin
            level = 0.0;
            pulse_end = 0.0;
        end
        @(cross(V(sigin) - 0.45, 0)) begin
            level = 0.9;
            pulse_end = $abstime + 4n;
        end
        @(timer(pulse_end))
            level = 0.0;
        V(sigout) <+ transition(level, 1n, 20p, 20p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"sigin": "sigin", "sigout": "sigout"}

    sim = Simulator()
    sim.add_source(
        "sigin",
        pwl(
            [0.0, 0.9e-9, 1.1e-9, 2.9e-9, 3.1e-9, 9e-9],
            [0.0, 0.0, 0.9, 0.9, 0.0, 0.0],
        ),
    )
    sim.add_model(model)
    sim.record("sigout")

    result = sim.run(
        tstop=9e-9,
        tstep=500e-12,
        record_step=100e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    held_high = result.signals["sigout"][
        (result.time >= 6.2e-9) & (result.time <= 7.7e-9)
    ]
    settled_low = result.signals["sigout"][result.time >= 8.2e-9]
    assert held_high.min() > 0.8
    assert settled_low.max() < 0.1


def test_dynamic_absolute_timer_past_target_disarms_after_fire() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module dynamic_timer_disarm(clk, out);
    input voltage clk;
    output voltage out;
    real next_tick_time;
    integer state;

    analog begin
        @(initial_step) begin
            next_tick_time = -1.0;
            state = 0;
        end

        @(cross(V(clk) - 0.5, +1) or timer(next_tick_time)) begin
            if (next_tick_time > 0.0 && $abstime >= next_tick_time) begin
                state = 0;
                next_tick_time = -1.0;
            end else begin
                state = 1;
                next_tick_time = $abstime + 1n;
            end
        end

        V(out) <+ state;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"clk": "clk", "out": "out"}

    sim = Simulator()
    sim.add_source("clk", pwl([0.0, 1e-9, 4e-9], [0.0, 1.0, 1.0]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=4e-9,
        tstep=500e-12,
        record_step=250e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    times = result.time
    out = result.signals["out"]

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert out[(times > 0.5e-9) & (times < 1.5e-9)].max() == pytest.approx(1.0)
    assert out[times > 2.5e-9].max() == pytest.approx(0.0)


def test_absolute_timer_reschedules_from_body_on_rust_fastpath() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module body_rescheduled_timer(out);
    output voltage out;
    real next_tick;
    integer count;

    analog begin
        @(initial_step) begin
            next_tick = 0.2n;
            count = 0;
        end

        @(timer(next_tick)) begin
            count = count + 1;
            next_tick = $abstime + 0.3n;
        end

        V(out) <+ count;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"out": "out"}

    sim = Simulator()
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=1.05e-9,
        tstep=500e-12,
        record_step=50e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    times = result.time
    out = result.signals["out"]

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert sim._perf_stats["rust_sim_program_rejections"] == 0
    assert sim._perf_stats["rust_full_model_required_correctness_fallbacks"] == 0
    assert out[times < 0.2e-9].max() == pytest.approx(0.0)
    assert out[(times > 0.22e-9) & (times < 0.48e-9)].max() == pytest.approx(1.0)
    assert out[(times > 0.52e-9) & (times < 0.78e-9)].max() == pytest.approx(2.0)
    assert out[times > 0.82e-9].max() == pytest.approx(3.0)


def test_absolute_timer_consumed_at_zero_does_not_rearm_from_cross_body() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module consumed_zero_timer(clk, enable, out);
    input voltage clk, enable;
    output voltage out;
    real next_tick_time;
    integer hit;

    analog begin
        @(initial_step) begin
            next_tick_time = 0.0;
            hit = 0;
        end

        @(cross(V(clk) - 0.5, +1) or timer(next_tick_time)) begin
            if (V(enable) < 0.5) begin
                next_tick_time = 0.0;
            end else if (next_tick_time > 0.0 && $abstime >= next_tick_time) begin
                hit = 1;
                next_tick_time = 0.0;
            end else begin
                next_tick_time = $abstime + 200p;
            end
        end

        V(out) <+ hit;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"clk": "clk", "enable": "enable", "out": "out"}

    sim = Simulator()
    sim.add_source("clk", pwl([0.0, 1e-9, 3e-9], [0.0, 1.0, 1.0]))
    sim.add_source("enable", pwl([0.0, 0.25e-9, 3e-9], [0.0, 1.0, 1.0]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=3e-9,
        tstep=250e-12,
        record_step=100e-12,
        max_step=250e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["out"].max() == pytest.approx(0.0, abs=1e-12)


def test_transition_target_changed_by_cross_is_applied_after_delay() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module delayed_cross_transition(clk, out);
    input clk;
    output out;
    electrical clk, out;
    integer state;

    analog begin
        @(initial_step)
            state = 0;

        @(cross(V(clk) - 0.5, +1))
            state = 1;

        V(out) <+ transition(state, 100p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"clk": "clk", "out": "out"}

    sim = Simulator()
    sim.add_source("clk", pwl([0.0, 1e-9, 3e-9], [0.0, 1.0, 1.0]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=2e-9,
        tstep=250e-12,
        record_step=100e-12,
        max_step=250e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    times = result.time
    out = result.signals["out"]

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert out[times <= 0.6e-9].max() == pytest.approx(0.0, abs=1e-12)
    assert out[(times > 0.6e-9) & (times < 0.65e-9)].max() == pytest.approx(
        1.0,
        abs=1e-12,
    )
    assert out[times > 1.0e-9].max() == pytest.approx(1.0, abs=1e-12)


def test_timer_due_with_same_time_reset_cross_uses_post_cross_source_state() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module peak_reset_timer_race(vin, rst, out);
    input voltage vin, rst;
    output voltage out;
    real peak;

    analog begin
        @(initial_step)
            peak = 0.0;

        @(timer(0, 500p)) begin
            if (V(rst) > 0.45)
                peak = 0.0;
            else if (V(vin) > peak)
                peak = V(vin);
        end

        @(cross(V(rst) - 0.45, +1))
            peak = 0.0;

        V(out) <+ peak;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "rst": "rst", "out": "out"}

    sim = Simulator()
    sim.add_source("vin", pwl([0.0, 2e-9], [0.8, 0.8]))
    sim.add_source("rst", pwl([0.0, 1e-9, 2e-9], [0.0, 0.45, 0.9]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=1.5e-9,
        tstep=500e-12,
        record_step=250e-12,
        max_step=500e-12,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    times = result.time
    out = result.signals["out"]

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert out[(times > 1.0e-9) & (times < 1.5e-9)].max() == pytest.approx(0.0)


def test_generic_source_cross_breakpoint_refines_inside_pwl_segment() -> None:
    src = """\
`include "disciplines.vams"

module source_cross_breakpoint(vin, out);
    input voltage vin;
    output voltage out;
    real state;

    analog begin
        @(initial_step)
            state = 1.0;

        @(cross(V(vin) - 0.55, +1))
            state = 0.0;

        V(out) <+ transition(state, 0, 10p, 10p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source(
        "vin",
        pwl([0.0, 105e-9, 105.1e-9, 120e-9], [0.51, 0.51, 0.7, 0.7]),
    )
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=106e-9,
        tstep=3e-9,
        record_step=2e-12,
        max_step=3e-9,
        rust_full_model_fastpath=False,
        skip_source_error_control=True,
    )

    expected_cross = 105e-9 + ((0.55 - 0.51) / (0.7 - 0.51)) * 0.1e-9
    times = result.time
    out = result.signals["out"]
    falling = times[(times > 105e-9) & (out < 0.99)]

    assert sim._perf_stats["model_breakpoint_clamps"] >= 1
    assert out[(times > 105e-9) & (times < expected_cross)].min() == pytest.approx(1.0)
    assert falling[0] < expected_cross + 4e-12
    assert out[times >= expected_cross + 12e-12].min() == pytest.approx(0.0, abs=1e-12)


def test_rust_source_cross_breakpoint_refines_inside_pwl_segment() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module rust_source_cross_breakpoint(vin, out);
    input voltage vin;
    output voltage out;
    real state;

    analog begin
        @(initial_step)
            state = 1.0;

        @(cross(V(vin) - 0.55, +1))
            state = 0.0;

        V(out) <+ transition(state, 0, 10p, 10p);
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source(
        "vin",
        pwl([0.0, 105e-9, 105.1e-9, 120e-9], [0.51, 0.51, 0.7, 0.7]),
    )
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=106e-9,
        tstep=3e-9,
        record_step=2e-12,
        max_step=3e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    expected_cross = 105e-9 + ((0.55 - 0.51) / (0.7 - 0.51)) * 0.1e-9
    times = result.time
    out = result.signals["out"]
    falling = times[(times > 105e-9) & (out < 0.99)]

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert sim._perf_stats["rust_sim_program_rejections"] == 0
    assert sim._perf_stats["rust_full_model_required_correctness_fallbacks"] == 0
    assert out[(times > 105e-9) & (times < expected_cross)].min() == pytest.approx(1.0)
    assert falling[0] < expected_cross + 4e-12
    assert out[times >= expected_cross + 12e-12].min() == pytest.approx(0.0, abs=1e-12)


def test_cross_body_samples_other_inputs_at_crossing_time() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"

module crossing_time_sampler(clk, vin, out);
    input voltage clk, vin;
    output voltage out;
    real sampled;

    analog begin
        @(initial_step)
            sampled = 0.0;

        @(cross(V(clk) - 0.5, +1)) begin
            if (V(vin) > 0.5)
                sampled = 1.0;
            else
                sampled = 0.0;
        end

        V(out) <+ sampled;
    end
endmodule
"""
    model_cls = compile_module(parse(src))
    model = model_cls()
    model.node_map = {"clk": "clk", "vin": "vin", "out": "out"}

    sim = Simulator()
    sim.add_source("clk", pwl([0.0, 1e-9], [0.0, 1.0]))
    sim.add_source("vin", pwl([0.0, 0.5e-9, 1e-9], [0.0, 0.0, 1.0]))
    sim.add_model(model)
    sim.record("out")

    result = sim.run(
        tstop=1e-9,
        tstep=1e-9,
        record_step=250e-12,
        max_step=1e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_enabled"] == 1
    assert result.signals["out"][-1] == pytest.approx(0.0, abs=1e-12)
