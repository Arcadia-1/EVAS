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
        "EVAS-COMP-EUNSUPPORTED",
        "EVAS-AHDL-W5003",
        "EVAS-AHDL-W5004",
        "EVAS-AHDL-W5005",
        "EVAS-AHDL-W5006",
        "EVAS-AHDL-W5007",
        "EVAS-AHDL-W5008",
        "EVAS-AHDL-W5010",
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
    assert LINT_RULE_SPECS["EVAS-COMP-EUNSUPPORTED"].oracle_status == "evas-specific"


def test_diagnostics_use_registered_rule_metadata():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module registry_metadata(out);
            output out;
            electrical out;
            integer mode;
            analog begin
                mode = 1;
                case (mode)
                    1: V(out) <+ transition(exp(mode), 100f);
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
                case (mode)
                    1: V(out) <+ transition(exp(mode), 100f);
                endcase
            end
        endmodule
    """)

    diags = lint_source(source, filename="loc.va")
    by_code = {diag.code: diag for diag in diags}

    assert by_code["EVAS-AHDL-W5011"].line == 7
    assert by_code["EVAS-AHDL-W5011"].column == 9
    assert by_code["EVAS-AHDL-W5003"].line == 8
    assert by_code["EVAS-AHDL-W5003"].column == 26
    assert by_code["EVAS-AHDL-W5018"].line == 8
    assert by_code["EVAS-AHDL-W5018"].column == 37
    assert by_code["EVAS-AHDL-W5003"].to_dict()["line"] == 8
    assert "loc.va:8:26" in by_code["EVAS-AHDL-W5003"].format_text()


def test_discrete_contribution_warns_like_ahdllint_5008():
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

    assert "EVAS-AHDL-W5008" in _codes(diags)
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


def test_conditional_slew_is_compat_error():
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

    assert "EVAS-COMP-E2151" in _codes(diags)
    assert has_compat_errors(diags)


def test_conditional_direct_contribution_does_not_error():
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

    assert "EVAS-AHDL-W5010" in _codes(diags)
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


def test_discrete_function_argument_warns_like_ahdllint_5018():
    source = textwrap.dedent("""\
        `include "disciplines.vams"
        module discrete_function_arg(out);
            output out;
            electrical out;
            integer mode;
            analog begin
                mode = 1;
                V(out) <+ exp(mode);
            end
        endmodule
    """)

    diags = lint_source(source)

    assert "EVAS-AHDL-W5018" in _codes(diags)
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

    assert "EVAS-COMP-EUNSUPPORTED" in _codes(diags)
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
