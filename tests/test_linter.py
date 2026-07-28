"""EVAS AHDL-style linter regression tests."""

import json
import runpy
import sys
import textwrap

import pytest

from evas.compiler.linter import (
    LINT_RULE_SPECS,
    has_compat_errors,
    lint_file,
    lint_source,
)
from evas.support_tiers import (
    AMS_DIGITAL,
    BEHAVIORAL_CONTINUOUS_TIME,
    BEHAVIORAL_EVENT,
    CONSERVATIVE_CURRENT_KCL,
    OUTSIDE_CURRENT_SCOPE,
)


def _codes(diags):
    return {diag.code for diag in diags}


def test_lint_rule_registry_covers_current_diagnostics():
    expected_codes = {
        "EVAS-COMP-ENETLIST",
        "EVAS-COMP-EINCLUDE",
        "EVAS-COMP-EFILE",
        "EVAS-COMP-EPREPROC",
        "EVAS-COMP-E2174",
        "EVAS-COMP-EPARSE",
        "EVAS-COMP-WPARSE",
        "EVAS-COMP-E1519",
        "EVAS-COMP-E2143",
        "EVAS-COMP-E2151",
        "EVAS-COMP-E2154",
        "EVAS-COMP-E2157",
        "EVAS-COMP-E2446",
        "EVAS-COMP-EKCL",
        "EVAS-COMP-EUNSUPPORTED",
        "EVAS-COMP-ESPECTRESTRICT",
        "EVAS-AHDL-W5003",
        "EVAS-AHDL-W5004",
        "EVAS-AHDL-W5005",
        "EVAS-AHDL-W5006",
        "EVAS-AHDL-W5007",
        "EVAS-AHDL-W5008",
        "EVAS-AHDL-W5011",
        "EVAS-AHDL-W5012",
        "EVAS-AHDL-W5013",
        "EVAS-AHDL-W5014",
        "EVAS-AHDL-W5017",
        "EVAS-AHDL-W5018",
        "EVAS-AHDL-W5023",
        "EVAS-AHDL-W5024",
        "EVAS-AHDL-W8007",
    }

    assert expected_codes == set(LINT_RULE_SPECS)
    assert LINT_RULE_SPECS["EVAS-COMP-E2143"].severity == "compat-error"
    assert LINT_RULE_SPECS["EVAS-AHDL-W5011"].category == "cadence-ahdl"
    assert LINT_RULE_SPECS["EVAS-COMP-EKCL"].severity == "static-warning"
    assert LINT_RULE_SPECS["EVAS-COMP-EKCL"].rule == "conservative-current-kcl-boundary"
    assert LINT_RULE_SPECS["EVAS-COMP-EUNSUPPORTED"].oracle_status == "evas-specific"
    assert LINT_RULE_SPECS["EVAS-COMP-ESPECTRESTRICT"].category == "spectre-compat"


def test_diagnostics_use_registered_rule_metadata():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module registry_metadata(out);
            output out;
            electrical out;
            integer mode;
            analog begin
                mode = 1;
                @(timer(0, 1n)) mode = 1 - mode;
                case (mode)
                    1: V(out) <+ transition(slew(mode, 1e9, 1e9), 100f);
                endcase
            end
        endmodule
    """)

    diags = lint_source(source)

    assert {"EVAS-AHDL-W5003", "EVAS-AHDL-W5006", "EVAS-AHDL-W5011", "EVAS-AHDL-W5018"} <= _codes(diags)
    for diag in diags:
        spec = LINT_RULE_SPECS[diag.code]
        assert diag.severity == spec.severity
        assert diag.rule == spec.rule
        assert diag.spectre_ids == list(spec.spectre_ids)


def test_lint_diagnostics_include_source_locations():
    source = textwrap.dedent("""\
        module loc(out);
            output out;
            electrical out;
            integer mode;
            analog begin
                mode = 1;
                @(timer(0, 1n)) mode = 1 - mode;
                case (mode)
                    1: V(out) <+ transition(slew(mode, 1e9, 1e9), 100f);
                endcase
            end
        endmodule
    """)

    diags = lint_source(source, filename="loc.va")
    by_code = {diag.code: diag for diag in diags}

    assert by_code["EVAS-AHDL-W5011"].line == 8
    assert by_code["EVAS-AHDL-W5011"].column == 9
    assert by_code["EVAS-AHDL-W5003"].line == 9
    assert by_code["EVAS-AHDL-W5003"].column == 26
    assert by_code["EVAS-AHDL-W5018"].line == 9
    assert by_code["EVAS-AHDL-W5018"].column == 37
    assert by_code["EVAS-AHDL-W5003"].to_dict()["line"] == 9
    assert "loc.va:9:26" in by_code["EVAS-AHDL-W5003"].format_text()


def test_timer_integer_direct_contribution_matches_oracle_negative_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module discrete_drive(clk, out);
            input clk;
            output out;
            electrical clk, out;
            integer state;
            analog begin
                @(cross(V(clk) - 0.5, +1)) state = 1 - state;
                V(out) <+ state;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_discrete_assignment_contribution_warns_like_ahdllint_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module discrete_target(inp, out);
            input inp;
            output out;
            electrical inp, out;
            real target;
            analog begin
                target = (V(inp) > 0.5) ? 1.0 : 0.0;
                V(out) <+ target;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" in _codes(diags)
    assert not has_compat_errors(diags)


def test_supply_scaled_discrete_assignment_warns_like_ahdllint_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module scaled_discrete(inp, vdd, vss, out);
            input inp, vdd, vss;
            output out;
            electrical inp, vdd, vss, out;
            real target;
            analog begin
                target = (V(inp) > 0.5) ? 1.0 : 0.0;
                V(out) <+ V(vss) + V(vdd, vss) * target;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" in _codes(diags)
    assert not has_compat_errors(diags)


def test_event_real_linear_contribution_matches_oracle_negative_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module event_real_linear(clk, out);
            input clk;
            output out;
            electrical clk, out;
            real target;
            analog begin
                @(initial_step) target = 0.25;
                @(cross(V(clk) - 0.5, +1)) target = 0.75;
                V(out) <+ 0.1 + 0.8 * target;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_abstime_phase_ramp_matches_oracle_negative_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module phase_ramp(clk, out);
            input clk;
            output out;
            electrical clk, out;
            real cycle_start, phase;
            analog begin
                @(initial_step) cycle_start = 0.0;
                @(cross(V(clk) - 0.5, +1)) cycle_start = $abstime;
                phase = $abstime - cycle_start;
                V(out) <+ phase / 2n;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_discrete_assignment_with_transition_does_not_warn_5008():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module transitioned_target(inp, out);
            input inp;
            output out;
            electrical inp, out;
            real target;
            analog begin
                target = (V(inp) > 0.5) ? 1.0 : 0.0;
                V(out) <+ transition(target, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_event_body_contribution_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module event_contribution(clk, out);
            input clk;
            output out;
            electrical clk, out;
            analog begin
                @(cross(V(clk) - 0.5, +1)) begin
                    V(out) <+ transition(1.0, 0, 1p, 1p);
                end
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2157" in _codes(diags)
    assert has_compat_errors(diags)


def test_conditional_transition_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module conditional_transition(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                if (V(inp) > 0.5)
                    V(out) <+ transition(1.0, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2143" in _codes(diags)
    assert has_compat_errors(diags)


def test_conditional_idt_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module conditional_idt(inp, en, out);
            input inp, en;
            output out;
            electrical inp, en, out;
            analog begin
                if (V(en) > 0.5)
                    V(out) <+ idt(V(inp));
                else
                    V(out) <+ 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2154" in _codes(diags)
    assert has_compat_errors(diags)


def test_conditional_slew_is_not_compile_blocked_for_target_spectre():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module conditional_slew(inp, en, out);
            input inp, en;
            output out;
            electrical inp, en, out;
            analog begin
                if (V(en) > 0.5)
                    V(out) <+ slew(V(inp), 1e9, 1e9);
                else
                    V(out) <+ 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2151" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_conditional_direct_contribution_matches_oracle_negative():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module conditional_direct(en, out);
            input en;
            output out;
            electrical en, out;
            analog begin
                if (V(en) > 0.5)
                    V(out) <+ 1.0;
                else
                    V(out) <+ 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5010" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_transition_continuous_input_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module transition_continuous(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ transition(V(inp), 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5007" in _codes(diags)
    assert not has_compat_errors(diags)


def test_transition_continuous_input_warns_through_assignment():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module transition_continuous_assignment(inp, out);
            input inp;
            output out;
            electrical inp, out;
            real target;
            analog begin
                target = V(inp);
                V(out) <+ transition(target, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5007" in _codes(diags)
    assert not has_compat_errors(diags)


def test_transition_event_latched_target_does_not_warn_5007():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module transition_event_latched(clk, inp, out);
            input clk, inp;
            output out;
            electrical clk, inp, out;
            real target;
            analog begin
                @(cross(V(clk) - 0.5, +1))
                    target = V(inp);
                V(out) <+ transition(target, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5007" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_tiny_transition_times_warn():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module tiny_transition(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ transition(1.0, 0, 100f, 100f);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert {"EVAS-AHDL-W5004", "EVAS-AHDL-W5005"} <= _codes(diags)
    assert not has_compat_errors(diags)


def test_transition_missing_rise_time_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module implicit_transition(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ transition(1.0, 0);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5003" in _codes(diags)
    assert not has_compat_errors(diags)


def test_tiny_positive_transition_delay_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module tiny_delay(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ transition(1.0, 100f, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5006" in _codes(diags)
    assert not has_compat_errors(diags)


def test_abstime_exact_equality_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module abstime_eq(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ ($abstime == 1n) ? 1.0 : 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5012" in _codes(diags)
    assert not has_compat_errors(diags)


def test_access_function_exact_equality_in_condition_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module branch_eq(inp, out);
            input inp;
            output out;
            electrical inp, out;
            real target;
            analog begin
                if (V(inp) == 0.5)
                    target = 1.0;
                else
                    target = 0.0;
                V(out) <+ transition(target, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5013" in _codes(diags)
    assert not has_compat_errors(diags)


def test_floor_in_contribution_warns_like_ahdllint_5014():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module floor_drive(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ floor(V(inp));
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5014" in _codes(diags)
    assert not has_compat_errors(diags)


def test_electrical_gnd_name_warns_like_ahdllint_5017():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module named_gnd(gnd, out);
            input gnd;
            output out;
            electrical gnd, out;
            analog begin
                V(out, gnd) <+ 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5017" in _codes(diags)
    assert not has_compat_errors(diags)


def test_integer_slew_argument_warns_like_ahdllint_5018():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module discrete_function_arg(clk, out);
            input clk;
            output out;
            electrical clk, out;
            integer state;
            analog begin
                @(initial_step) state = 0;
                @(timer(0, 1n)) state = 1 - state;
                V(out) <+ slew(state, 1e9, 1e9);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5018" in _codes(diags)
    assert not has_compat_errors(diags)


def test_ordinary_math_integer_argument_matches_oracle_negative_5018():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module ordinary_math_arg(out);
            output out;
            electrical out;
            integer code;
            analog begin
                code = 3;
                V(out) <+ pow(1.8, code);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5018" not in _codes(diags)
    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_event_real_math_argument_matches_oracle_negative_5018():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module event_real_math_arg(clk, out);
            input clk;
            output out;
            electrical clk, out;
            real target;
            analog begin
                @(initial_step) target = 1.0;
                @(cross(V(clk) - 0.5, +1)) target = 2.0;
                V(out) <+ ln(target);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5018" not in _codes(diags)
    assert "EVAS-AHDL-W5008" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_integer_assignment_from_real_warns_like_ahdllint_5023():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module implicit_integer_cast(inp, out);
            input inp;
            output out;
            electrical inp, out;
            integer code;
            analog begin
                code = V(inp);
                V(out) <+ transition(code, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5023" in _codes(diags)
    assert not has_compat_errors(diags)


def test_stop_finish_inside_loop_warns_like_ahdllint_5024():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module stop_in_loop(out);
            output out;
            electrical out;
            integer i;
            analog begin
                i = 0;
                while (i < 2) begin
                    $finish;
                    i = i + 1;
                end
                V(out) <+ 0.0;
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5024" in _codes(diags)
    assert not has_compat_errors(diags)


def test_case_without_default_warns_like_ahdllint_5011():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module case_no_default(out);
            output out;
            electrical out;
            integer state;
            real target;
            analog begin
                state = 1;
                case (state)
                    0: target = 0.0;
                    1: target = 1.0;
                endcase
                V(out) <+ transition(target, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5011" in _codes(diags)
    assert not has_compat_errors(diags)


def test_nested_ddt_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module nested_ddt(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ ddt(ddt(V(inp)));
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E1519" in _codes(diags)
    assert has_compat_errors(diags)


def test_unsupported_function_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module unsupported_fn(out);
            output out;
            electrical out;
            real x;
            analog begin
                x = made_up_filter(1.0);
                V(out) <+ x;
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EUNSUPPORTED")

    assert "EVAS-COMP-EUNSUPPORTED" in _codes(diags)
    assert diag.support_tier == OUTSIDE_CURRENT_SCOPE
    assert f"support-tier: {OUTSIDE_CURRENT_SCOPE}" in diag.format_text()
    assert has_compat_errors(diags)


def test_absdelay_voltage_transport_delay_subset_is_supported():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module absdelay_ct(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ absdelay(V(inp), 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-EUNSUPPORTED" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_absdelay_rejects_wrong_arity():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module bad_absdelay(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ absdelay(V(inp));
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EUNSUPPORTED")

    assert "absdelay() expects 2 or 3 arguments" in diag.message
    assert diag.support_tier == BEHAVIORAL_CONTINUOUS_TIME
    assert has_compat_errors(diags)


def test_absdelay_rejects_negative_static_delay():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module bad_absdelay(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ absdelay(V(inp), -1n);
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EUNSUPPORTED")

    assert "absdelay() delay must be nonnegative" in diag.message
    assert diag.support_tier == BEHAVIORAL_CONTINUOUS_TIME
    assert has_compat_errors(diags)


def test_strict_spectre_rejects_parameter_default_on_exclusive_bound():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module exclusive_bound(out);
            output out;
            electrical out;
            parameter real tdel = 0 from (0:inf);
            analog begin
                V(out) <+ tdel;
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)

    assert not has_compat_errors(extension_diags)
    assert has_compat_errors(strict_diags)
    assert any(
        "parameter tdel default 0" in diag.message
        and "exclusive lower bound" in diag.message
        for diag in strict_diags
    )


def test_strict_spectre_accepts_parameter_default_inside_range():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module inside_bound(out);
            output out;
            electrical out;
            parameter real tdel = 1p from (0:inf);
            analog begin
                V(out) <+ tdel;
            end
        endmodule
    """)

    assert not has_compat_errors(lint_source(source, strict_spectre=True))


def test_strict_spectre_rejects_typed_old_style_function_input():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module typed_function(out);
            output out;
            electrical out;
            analog function real clip01;
                input real y;
                begin
                    clip01 = y;
                end
            endfunction
            analog begin
                V(out) <+ clip01(0.5);
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)

    assert not has_compat_errors(extension_diags)
    assert has_compat_errors(strict_diags)
    assert any("input y; real y;" in diag.message for diag in strict_diags)


def test_strict_spectre_accepts_split_old_style_function_input_type():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module split_function(out);
            output out;
            electrical out;
            analog function real clip01;
                input y;
                real y;
                begin
                    clip01 = y;
                end
            endfunction
            analog begin
                V(out) <+ clip01(0.5);
            end
        endmodule
    """)

    assert not has_compat_errors(lint_source(source, strict_spectre=True))


def test_strict_spectre_rejects_source_local_custom_discipline():
    source = textwrap.dedent("""\
        discipline electrical
            potential Voltage;
            flow Current;
        enddiscipline
        module missing_semicolon(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ 0.0;
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)

    assert not has_compat_errors(extension_diags)
    assert has_compat_errors(strict_diags)
    assert any(
        "custom nature/discipline" in diag.message
        and "disciplines.vams" in diag.message
        for diag in strict_diags
    )


def test_strict_spectre_accepts_builtin_discipline_include():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module terminated_header(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ 0.0;
            end
        endmodule
    """)

    assert not has_compat_errors(lint_source(source, strict_spectre=True))


def test_unsupported_random_distribution_is_behavioral_event_tiered():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module unsupported_dist(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ $rdist_gamma(17, 2.0, 1.0);
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EUNSUPPORTED")

    assert diag.support_tier == BEHAVIORAL_EVENT
    assert "$rdist_gamma()" in diag.message
    assert f"support-tier: {BEHAVIORAL_EVENT}" in diag.format_text()
    assert has_compat_errors(diags)


def test_current_contribution_is_kcl_tier_boundary_warning():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module current_drive(out);
            output out;
            electrical out;
            analog begin
                I(out) <+ 1u;
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EKCL")

    assert diag.support_tier == CONSERVATIVE_CURRENT_KCL
    assert diag.severity == "static-warning"
    assert "current contribution I(...) <+ ..." in diag.message
    assert f"support-tier: {CONSERVATIVE_CURRENT_KCL}" in diag.format_text()
    assert not has_compat_errors(diags)


def test_current_probe_is_kcl_tier_boundary_warning():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module current_probe(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) <+ I(inp);
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EKCL")

    assert diag.support_tier == CONSERVATIVE_CURRENT_KCL
    assert diag.severity == "static-warning"
    assert "current probe I(...)" in diag.message
    assert not has_compat_errors(diags)


def test_indirect_branch_is_kcl_tier_boundary_warning():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module indirect_branch(inp, out);
            input inp;
            output out;
            electrical inp, out;
            analog begin
                V(out) : V(inp) == 0;
            end
        endmodule
    """)

    diags = lint_source(source)
    diag = next(diag for diag in diags if diag.code == "EVAS-COMP-EKCL")

    assert diag.support_tier == CONSERVATIVE_CURRENT_KCL
    assert diag.severity == "static-warning"
    assert "indirect branch equation" in diag.message
    assert not has_compat_errors(diags)


def test_unsupported_digital_procedural_block_is_ams_digital_tier():
    source = textwrap.dedent("""\
        module initial_block(out);
            output out;
            logic out;
            initial begin
                out = 1'b0;
            end
        endmodule
    """)

    diags = lint_source(source, filename="initial_block.va")
    assert len(diags) == 1
    diag = diags[0]

    assert diag.code == "EVAS-COMP-EPARSE"
    assert diag.support_tier == AMS_DIGITAL
    assert f"support-tier: {AMS_DIGITAL}" in diag.format_text()
    assert has_compat_errors(diags)


def test_conditional_timer_warns():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module conditional_timer(en, out);
            input en;
            output out;
            electrical en, out;
            real x;
            analog begin
                if (V(en) > 0.5)
                    @(timer(1n)) x = 1.0;
                V(out) <+ transition(x, 0, 1n, 1n);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W8007" in _codes(diags)
    assert not has_compat_errors(diags)


def test_strict_spectre_rejects_nested_timer_event_control():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module nested_timer(clk, out);
            input clk;
            output out;
            electrical clk, out;
            integer fired;
            analog begin
                @(cross(V(clk) - 0.5, +1)) begin
                    @(timer($abstime + 1n))
                        fired = fired + 1;
                end
                V(out) <+ fired;
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert any(
        "nested timer event control" in diag.message
        for diag in strict_diags
    )
    assert has_compat_errors(strict_diags)


def test_strict_spectre_allows_explicit_armed_root_timer():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module armed_root_timer(clk, out);
            input clk;
            output out;
            electrical clk, out;
            real deadline;
            integer armed, fired;
            analog begin
                @(initial_step) begin
                    deadline = 1.0;
                    armed = 0;
                    fired = 0;
                end
                @(cross(V(clk) - 0.5, +1)) begin
                    deadline = $abstime + 1n;
                    armed = 1;
                end
                @(timer(deadline)) begin
                    if (armed) begin
                        fired = fired + 1;
                        armed = 0;
                    end
                end
                V(out) <+ fired;
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)

    assert not any(
        "nested timer event control" in diag.message
        for diag in strict_diags
    )
    assert not has_compat_errors(strict_diags)


def test_variable_electrical_range_is_compat_error():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module variable_range(inp, out);
            input inp;
            output out;
            electrical inp, out;
            integer n;
            electrical [n-1:0] tmp;
            analog begin
                V(tmp[0]) <+ V(inp);
                V(out) <+ V(tmp[0]);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2446" in _codes(diags)
    assert has_compat_errors(diags)


def test_parameter_electrical_range_is_not_misclassified_as_vacomp_2446():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module parameter_range(inp, out);
            input inp;
            output out;
            electrical inp, out;
            parameter integer N = 4;
            electrical [N-1:0] tmp;
            analog begin
                V(tmp[0]) <+ V(inp);
                V(out) <+ V(tmp[0]);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2446" not in _codes(diags)
    assert "EVAS-COMP-EPARSE" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_constant_electrical_range_is_allowed():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module constant_range(inp, out);
            input inp;
            output out;
            electrical inp, out;
            electrical [3:0] tmp;
            analog begin
                V(tmp[0]) <+ V(inp);
                V(out) <+ V(tmp[0]);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-COMP-E2446" not in _codes(diags)
    assert not has_compat_errors(diags)


def test_strict_spectre_rejects_ams_bridge_tokens():
    source = textwrap.dedent("""\
        module ams_bridge(clk, y);
            input logic clk;
            output wreal y;
            assign y = clk;
            always @(posedge clk) y = 1;
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert has_compat_errors(strict_diags)
    assert all(
        diag.support_tier == AMS_DIGITAL
        for diag in strict_diags
        if diag.code == "EVAS-COMP-ESPECTRESTRICT"
    )
    assert "logic" in messages
    assert "wreal" in messages
    assert "continuous assign" in messages
    assert "always block" in messages


def test_strict_spectre_rejects_extension_only_behavioral_constructs():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module strict_ext(out);
            output [3:0] out;
            electrical [3:0] out;
            integer i;

            task update;
                begin
                    i = i + 1;
                end
            endtask

            analog begin
                do i = i + 1; while (i < 2);
                V(out[i]) <+ $rdist_t(7, 3.0);
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert has_compat_errors(strict_diags)
    assert "task/endtask" in messages
    assert "do while" in messages
    assert "runtime electrical-node indexing" in messages
    assert "$rdist_t()" in messages
    assert any(
        diag.support_tier == BEHAVIORAL_EVENT
        for diag in strict_diags
        if diag.code == "EVAS-COMP-ESPECTRESTRICT"
    )


def test_strict_spectre_rejects_seeded_rdist_parity_gaps_except_normal():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module strict_random(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ $rdist_exponential(17, 2.0)
                        + $rdist_poisson(17, 4.0)
                        + $rdist_normal(17, 0.0, 1.0)
                        + $rdist_erlang(17, 3.0, 6.0);
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert "$rdist_exponential()" in messages
    assert "$rdist_poisson()" in messages
    assert "$rdist_normal()" not in messages
    assert "$rdist_erlang()" in messages
    assert "seeded Spectre PRNG sequence parity is not certified" in messages


def test_strict_spectre_allows_target_accepted_rdist_normal_and_gnd_warning():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module strict_random_gnd(gnd, out);
            input gnd;
            output out;
            electrical gnd, out;
            integer seed;
            analog begin
                seed = 17;
                V(out, gnd) <+ $rdist_normal(seed, 0.0, 1.0);
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-AHDL-W5017" in _codes(strict_diags)
    assert "$rdist_normal()" not in messages
    assert not has_compat_errors(strict_diags)


def test_strict_spectre_allows_static_generate_genvar_elaboration():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module strict_generate(out);
            output [1:0] out;
            electrical [1:0] out;
            genvar i;
            generate for (i = 0; i < 2; i = i + 1) begin
                analog begin
                    V(out[i]) <+ 0.0;
                end
            end endgenerate
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "generate/genvar static elaboration" not in messages
    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(strict_diags)
    assert not has_compat_errors(strict_diags)


def test_strict_spectre_rejects_integer_select_concat_parity_gaps():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module strict_vectors(out);
            output out;
            electrical out;
            integer code_q;
            integer count_q;
            integer window_q;
            analog begin
                window_q = code_q[3:1];
                code_q = {2'b10, count_q[1:0]};
                V(out) <+ code_q + window_q;
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert "integer part-select" in messages
    assert "integer select concatenation" in messages
    assert has_compat_errors(strict_diags)


def test_strict_spectre_allows_static_electrical_indexing():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module static_idx(out);
            output [3:0] out;
            electrical [3:0] out;
            analog begin
                V(out[0]) <+ 1.0;
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(strict_diags)
    assert not has_compat_errors(strict_diags)


def test_strict_spectre_does_not_treat_comment_or_string_backticks_as_macros():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module literal_backtick(out);
            output out;
            electrical out;
            string message;
            analog begin
                // `not_a_macro
                message = "`also_not_a_macro";
                V(out) <+ 0.0;
            end
        endmodule
    """)

    diagnostics = lint_source(source, strict_spectre=True)

    assert not any(
        "unexpanded Verilog-A macro" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_strict_spectre_rejects_parameter_default_on_exclusive_range_bound():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module exclusive_bound(out);
            output out;
            electrical out;
            parameter real tdel = 0 from (0:inf);
            analog begin
                V(out) <+ transition(1.0, tdel, 20p, 20p);
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert "parameter tdel default 0 violates the exclusive lower bound 0" in messages
    assert has_compat_errors(strict_diags)


def test_strict_spectre_allows_parameter_default_on_inclusive_range_bound():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module inclusive_bound(out);
            output out;
            electrical out;
            parameter real tdel = 0 from [0:inf);
            analog begin
                V(out) <+ transition(1.0, tdel, 20p, 20p);
            end
        endmodule
    """)

    strict_diags = lint_source(source, strict_spectre=True)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(strict_diags)
    assert not has_compat_errors(strict_diags)


def test_strict_spectre_rejects_realtime_call_parentheses():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module realtime_call(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ sin($realtime());
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert "standalone Spectre requires $realtime without parentheses" in messages
    assert has_compat_errors(strict_diags)


def test_strict_spectre_rejects_typed_subprogram_arg_declaration():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module typed_arg(out);
            output out;
            electrical out;
            analog function real clip01;
                input real x;
                begin
                    clip01 = x;
                end
            endfunction
            analog begin
                V(out) <+ clip01(1.0);
            end
        endmodule
    """)

    extension_diags = lint_source(source)
    strict_diags = lint_source(source, strict_spectre=True)
    messages = "\n".join(diag.message for diag in strict_diags)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert "typed subprogram argument declaration" in messages
    assert has_compat_errors(strict_diags)


def test_lint_spectre_netlist_follows_ahdl_include(tmp_path):
    va_file = tmp_path / "model.va"
    va_file.write_text(textwrap.dedent("""\
        `include "disciplines.vams"
        module model(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ transition(1.0, 0, 100f, 100f);
            end
        endmodule
    """))
    scs_file = tmp_path / "tb.scs"
    scs_file.write_text(textwrap.dedent("""\
        I0 (out) model
        tran tran stop=1n
        ahdl_include "model.va"
        save out
    """))

    diags = lint_file(scs_file)

    assert {"EVAS-AHDL-W5004", "EVAS-AHDL-W5005"} <= _codes(diags)


def test_lint_spectre_netlist_strict_rejects_fake_ground_instance(tmp_path):
    scs_file = tmp_path / "tb.scs"
    scs_file.write_text(
        "simulator lang=spectre\ngnd (0)\ntran tran stop=1n\n",
        encoding="utf-8",
    )

    extension_diags = lint_file(scs_file)
    strict_diags = lint_file(scs_file, strict_spectre=True)

    assert "EVAS-COMP-ESPECTRESTRICT" not in _codes(extension_diags)
    assert "EVAS-COMP-ESPECTRESTRICT" in _codes(strict_diags)
    assert any("use `global 0`" in diag.message for diag in strict_diags)


def test_lint_spectre_netlist_strict_accepts_global_ground(tmp_path):
    scs_file = tmp_path / "tb.scs"
    scs_file.write_text(
        "simulator lang=spectre\nglobal 0\ntran tran stop=1n\n",
        encoding="utf-8",
    )

    assert not has_compat_errors(lint_file(scs_file, strict_spectre=True))


def test_lint_spectre_netlist_does_not_fallback_to_include_basename(tmp_path):
    (tmp_path / "model.va").write_text(
        "module model(out); output out; electrical out; endmodule\n",
        encoding="utf-8",
    )
    scs_file = tmp_path / "tb.scs"
    scs_file.write_text(
        'simulator lang=spectre\nahdl_include "missing/model.va"\n',
        encoding="utf-8",
    )

    diags = lint_file(scs_file)

    assert "EVAS-COMP-EINCLUDE" in _codes(diags)
    assert any("missing/model.va" in diag.message for diag in diags)


def test_lint_cli_json_exits_nonzero_on_compat_error(tmp_path, monkeypatch, capsys):
    va_file = tmp_path / "bad.va"
    va_file.write_text(textwrap.dedent("""\
        `include "disciplines.vams"
        module bad(out);
            output out;
            electrical out;
            analog begin
                V(out) <+ made_up_filter(1.0);
            end
        endmodule
    """))
    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "lint", str(va_file), "--format", "json"],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data[0]["code"] == "EVAS-COMP-EUNSUPPORTED"
    assert data[0]["support_tier"] == OUTSIDE_CURRENT_SCOPE


def test_lint_cli_json_spectre_strict_exits_nonzero(tmp_path, monkeypatch, capsys):
    va_file = tmp_path / "ams_bridge.va"
    va_file.write_text(textwrap.dedent("""\
        module ams_bridge(clk, y);
            input clk;
            logic clk;
            output y;
            wreal y;
            assign y = clk;
            always @(posedge clk) y = 1;
        endmodule
    """))
    monkeypatch.setattr(
        sys,
        "argv",
        ["evas", "lint", str(va_file), "--spectre-strict", "--format", "json"],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("evas", run_name="__main__")

    assert excinfo.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert {item["code"] for item in data} == {"EVAS-COMP-ESPECTRESTRICT"}
    assert {item["support_tier"] for item in data} == {AMS_DIGITAL}
