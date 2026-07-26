from __future__ import annotations

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.rust_program import build_source_record_rust_program


def _compile_report(source: str):
    model_cls = compile_module(parse(source))
    model = model_cls()
    module = model_cls._module_ast
    model.node_map = {str(name): str(name).upper() for name in module.ports}
    return build_source_record_rust_program(
        sources=(),
        recorded_signals=("OUT",),
        models=(model,),
    )


def test_periodic_timer_accepts_initialized_local_state_never_assigned_in_body():
    report = _compile_report(
        """\
`include "disciplines.vams"
module timer_declared_constants(out);
    output voltage out;
    electrical out;

    real period = 20n;
    real half_period = 10n;
    real level = 0;

    analog begin
        @(timer(half_period / 2.0, period))
            level = 1.0 - level;
        V(out) <+ level;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_periodic_timer_accepts_initialized_local_state_assigned_in_body():
    report = _compile_report(
        """\
`include "disciplines.vams"
module timer_mutable_state(out);
    output voltage out;
    electrical out;

    real period = 20n;
    real level = 0;

    analog begin
        @(initial_step)
            period = 10n;
        @(timer(0, period))
            level = 1.0 - level;
        V(out) <+ level;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_periodic_timer_accepts_runtime_state_period_like_spectre():
    report = _compile_report(
        """\
`include "disciplines.vams"
module timer_runtime_period(clk, out);
    input voltage clk;
    output voltage out;
    electrical clk, out;

    parameter real threshold = 0.5;
    parameter real pulse_width = 10n;
    integer state = 0;
    integer trigger_count = 0;

    analog begin
        @(cross(V(clk) - threshold, +1)) begin
            state = 1;
            trigger_count = trigger_count + 1;
        end
        @(timer(pulse_width, trigger_count))
            state = 0;
        V(out) <+ state;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_nested_absolute_timer_in_cross_body_is_lowered_for_spectre_compatibility():
    report = _compile_report(
        """\
`include "disciplines.vams"
module nested_timer_compile(clk, out);
    input voltage clk;
    output voltage out;
    electrical clk, out;

    parameter real delay = 1n;
    integer state = 0;

    analog begin
        @(cross(V(clk) - 0.5, +1)) begin
            @(timer($abstime + delay))
                state = 1;
        end
        V(out) <+ state;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_nested_absolute_timer_inside_conditional_is_lowered():
    report = _compile_report(
        """\
`include "disciplines.vams"
module conditional_nested_timer(ref, fb, out);
    input voltage ref, fb;
    output voltage out;
    electrical ref, fb, out;

    parameter real delay = 1n;
    real deadline = 1.0;
    integer ref_seen = 0;
    integer fb_seen = 0;
    integer pending = 0;

    analog begin
        @(cross(V(ref) - 0.5, +1)) begin
            ref_seen = 1;
            if (fb_seen && !pending) begin
                pending = 1;
                deadline = $abstime + delay;
                @(timer(deadline)) begin
                    ref_seen = 0;
                    fb_seen = 0;
                    pending = 0;
                end
            end
        end
        V(out) <+ ref_seen;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_two_level_nested_absolute_timers_are_lowered():
    report = _compile_report(
        """\
`include "disciplines.vams"
module double_nested_timer(clk, out);
    input voltage clk;
    output voltage out;
    electrical clk, out;

    parameter real first_delay = 1n;
    parameter real second_delay = 2n;
    real first_deadline = 1.0;
    real second_deadline = 1.0;
    integer state = 0;

    analog begin
        @(cross(V(clk) - 0.5, +1)) begin
            first_deadline = $abstime + first_delay;
            @(timer(first_deadline)) begin
                state = 1;
                second_deadline = $abstime + second_delay;
                @(timer(second_deadline))
                    state = 0;
            end
        end
        V(out) <+ state;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_nested_timer_with_sequential_continuation_is_lowered():
    report = _compile_report(
        """\
`include "disciplines.vams"
module nested_timer_with_tail(clk, out);
    input voltage clk;
    output voltage out;
    electrical clk, out;

    real deadline = 1.0;
    integer state = 0;

    analog begin
        @(cross(V(clk) - 0.5, +1)) begin
            deadline = $abstime + 1n;
            @(timer(deadline))
                state = 1;
            state = 2;
        end
        V(out) <+ state;
    end
endmodule
"""
    )

    assert report.supported, report.reasons


def test_periodic_timer_rejects_dynamic_node_expression():
    report = _compile_report(
        """\
`include "disciplines.vams"
module timer_dynamic_node(clk, out);
    input voltage clk;
    output voltage out;
    electrical clk, out;

    real period = 20n;
    real level = 0;

    analog begin
        @(timer(V(clk), period))
            level = 1.0 - level;
        V(out) <+ level;
    end
endmodule
"""
    )

    assert not report.supported
    assert any(reason.endswith(":event_due_not_lowered") for reason in report.reasons)


@pytest.mark.parametrize(
    "scan_statement",
    (
        '$fscanf(fd, "%f", period);',
        'nread = $fscanf(fd, "%f", period);',
    ),
)
def test_periodic_timer_accepts_state_mutated_through_scanf_side_effect(
    scan_statement: str,
):
    report = _compile_report(
        f"""\
`include "disciplines.vams"
module timer_scanf_mutable_state(out);
    output voltage out;
    electrical out;

    integer fd = 0;
    integer nread = 0;
    real period = 20n;
    real level = 0;

    analog begin
        @(timer(1n, period)) begin
            {scan_statement}
            level = 1.0 - level;
        end
        V(out) <+ level;
    end
endmodule
"""
    )

    assert report.supported, report.reasons
