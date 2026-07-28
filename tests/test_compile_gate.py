import textwrap
from pathlib import Path

import pytest

from evas.netlist.runner import compile_spectre_netlist
from evas.simulator.engine import Simulator


def _write(path: Path, text: str) -> Path:
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _basic_va(tmp_path: Path, *, name: str = "buf", body: str = "V(out) <+ V(in);") -> Path:
    return _write(
        tmp_path / f"{name}.va",
        f"""\
        `include "disciplines.vams"

        module {name}(in, out);
            input in;
            output out;
            electrical in, out;

            analog begin
                {body}
            end
        endmodule
        """,
    )


def _write_nonansi_combined_va(path: Path, name: str = "nonansi_bad") -> Path:
    return _write(
        path,
        f"""\
        `include "disciplines.vams"

        module {name}(data, clk, retimed_data, up, down);
            input electrical data;
            input electrical clk;
            input electrical retimed_data;
            output electrical up;
            output electrical down;
            electrical data, clk, retimed_data, up, down;

            analog begin
                V(up) <+ V(data);
            end
        endmodule
        """,
    )


def _netlist(tmp_path: Path, body: str) -> Path:
    return _write(
        tmp_path / "tb.scs",
        f"""\
        simulator lang=spectre
        global 0

        {body}
        """,
    )


def test_compile_gate_accepts_source_only_netlist_without_tran(tmp_path):
    scs = _netlist(
        tmp_path,
        """\
        Vin (in 0) vsource dc=0.9
        save in
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.stage == "ok"
    assert result.netlist is not None
    assert result.simulator is not None
    assert result.rust_report is not None
    assert result.rust_report.supported
    assert result.rust_report.program is not None
    assert result.rust_report.program.record_names == ("in",)


def test_compile_gate_strict_rejects_fake_ground_instance(tmp_path):
    scs = _netlist(
        tmp_path,
        """\
        gnd (0)
        Vin (in 0) vsource dc=0.9
        save in
        """,
    )

    extension_result = compile_spectre_netlist(str(scs))
    strict_result = compile_spectre_netlist(str(scs), spectre_strict=True)

    assert extension_result.ok
    assert not strict_result.ok
    assert strict_result.stage == "strict_netlist"
    assert any(
        "use `global 0`" in diag.message
        for diag in strict_result.diagnostics
    )


def test_compile_gate_strict_accepts_global_ground_declaration(tmp_path):
    scs = _netlist(
        tmp_path,
        """\
        Vin (in 0) vsource dc=0.9
        save in
        """,
    )

    result = compile_spectre_netlist(str(scs), spectre_strict=True)

    assert result.ok
    assert result.stage == "ok"


def test_compile_gate_rejects_absdelay_without_python_fallback(tmp_path):
    _basic_va(
        tmp_path,
        name="delayed_buf",
        body="V(out) <+ absdelay(V(in), 1n);",
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "delayed_buf.va"
        Vin (in 0) vsource dc=0.9
        XDUT (in out) delayed_buf
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any(
        "absdelay()" in diag.message
        for diag in result.diagnostics
    )


def test_compile_gate_reports_missing_ahdl_include(tmp_path):
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "missing.va"
        XDUT (in out) buf
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "ahdl_include"
    assert any("Cannot find VA file" in diag.message for diag in result.diagnostics)


def test_compile_gate_does_not_fallback_to_include_basename(tmp_path):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "models/buf.va"
        XDUT (in out) buf
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "ahdl_include"
    assert any("models/buf.va" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_duplicate_module_names(tmp_path):
    _write(
        tmp_path / "dups.va",
        """\
        `include "disciplines.vams"
        module dup(in, out);
            input in; output out; electrical in, out;
            analog begin V(out) <+ V(in); end
        endmodule
        module dup(in, out);
            input in; output out; electrical in, out;
            analog begin V(out) <+ V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "dups.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) dup
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "model_registry"
    assert any("Duplicate Verilog-A module" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_unknown_instance_master(tmp_path):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "buf.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) missing_model
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "instance_bind"
    assert any("Model missing_model not found" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_unexpanded_macro_call_by_default(tmp_path):
    _basic_va(
        tmp_path,
        name="smooth",
        body="V(out) <+ `tanh(V(in));",
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "smooth.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) smooth
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any("unexpanded Verilog-A macro" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_parameter_as_branch_node_by_default(tmp_path):
    _write(
        tmp_path / "threshold_probe.va",
        """\
        `include "disciplines.vams"
        module threshold_probe(in, out);
            input in; output out; electrical in, out;
            parameter real vth = 0.45;
            analog begin V(out) <+ V(vth); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "threshold_probe.va"
        XDUT (in out) threshold_probe
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any("parameter/variable as an electrical branch node" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_parameter_as_branch_reference_by_default(tmp_path):
    _write(
        tmp_path / "rail_ref_probe.va",
        """\
        `include "disciplines.vams"
        module rail_ref_probe(in, out);
            input in; output out; electrical in, out;
            parameter real vss = 0.0;
            analog begin V(out, vss) <+ V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "rail_ref_probe.va"
        XDUT (in out) rail_ref_probe
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any("parameter/variable as an electrical branch node" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_parameter_as_instance_terminal_by_default(tmp_path):
    _write(
        tmp_path / "hier_terminal_probe.va",
        """\
        `include "disciplines.vams"
        module parent(out);
            output out; electrical out;
            parameter real vcm = 0.45;
            child u_child(.in(vcm), .out(out));
        endmodule

        module child(in, out);
            input in; output out; electrical in, out;
            analog begin V(out) <+ V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "hier_terminal_probe.va"
        XDUT (out) parent
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any("parameter/variable as an instance terminal" in diag.message for diag in result.diagnostics)


def test_compile_gate_ignores_unknown_instance_parameter_with_spectre_strict(tmp_path):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "buf.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) buf missing_param=2
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs), spectre_strict=True)

    assert result.ok
    assert result.stage == "ok"
    assert result.warnings == 1
    assert any(
        "Spectre-tolerant instance parameter override" in diag.message
        for diag in result.diagnostics
    )
    assert result.simulator is not None
    model = result.simulator.models[0]
    assert "missing_param" not in model.params
    assert "missing_param" not in model._given_params


def test_compile_gate_ignores_unknown_instance_parameter_by_default(tmp_path):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "buf.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) buf missing_param=2
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.stage == "ok"
    assert result.warnings == 1
    assert any(
        "Spectre-tolerant instance parameter override" in diag.message
        for diag in result.diagnostics
    )
    assert result.simulator is not None
    model = result.simulator.models[0]
    assert "missing_param" not in model.params
    assert "missing_param" not in model._given_params


@pytest.mark.parametrize("spectre_strict", [False, True])
def test_compile_gate_rejects_descendant_only_instance_parameter(
    tmp_path,
    spectre_strict,
):
    _write(
        tmp_path / "hier.va",
        """\
        `include "disciplines.vams"

        module parent(in, out);
            input in; output out; electrical in, out;
            child u_child(.in(in), .out(out));
        endmodule

        module child(in, out);
            input in; output out; electrical in, out;
            parameter real tap_limit = 1.0;
            analog begin V(out) <+ tap_limit * V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "hier.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) parent tap_limit=2
        save out
        """,
    )

    result = compile_spectre_netlist(
        str(scs),
        spectre_strict=spectre_strict,
    )

    assert not result.ok
    assert result.stage == "instance_bind"
    assert any(
        "inherited-parameter override 'tap_limit'" in diag.message
        for diag in result.diagnostics
    )


def test_compile_gate_lowers_localparam_as_non_overridable_constant(tmp_path):
    _write(
        tmp_path / "localparam_gain.va",
        """\
        `include "disciplines.vams"
        module localparam_gain(in, out);
            input in; output out; electrical in, out;
            parameter real gain = 2.0;
            localparam real scaled_gain = gain / 2.0;
            analog begin V(out) <+ scaled_gain * V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "localparam_gain.va"
        Vin (in 0) vsource dc=1
        XDUT (in out) localparam_gain gain=4
        XREF (in out_ref) localparam_gain
        save out out_ref
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.simulator is not None
    model = result.simulator.models[0]
    assert model.params["gain"] == pytest.approx(4.0)
    assert model.params["scaled_gain"] == pytest.approx(2.0)
    assert "scaled_gain" not in model._given_params


def test_compile_gate_refreshes_dependent_parameter_defaults_after_override(
    tmp_path,
):
    _write(
        tmp_path / "dependent_params.va",
        """\
        `include "disciplines.vams"
        module dependent_params(in, out);
            input in; output out; electrical in, out;
            parameter real gain = 2.0;
            localparam real half_gain = gain / 2.0;
            parameter real derived_gain = half_gain + 1.0;
            analog begin V(out) <+ derived_gain * V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "dependent_params.va"
        Vin (in 0) vsource dc=1
        XDEFAULT (in out_default) dependent_params gain=4
        XOVERRIDE (in out_override) dependent_params gain=4 derived_gain=9
        save out_default out_override
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    default_model, override_model = result.simulator.models
    assert default_model.params["half_gain"] == pytest.approx(2.0)
    assert default_model.params["derived_gain"] == pytest.approx(3.0)
    assert override_model.params["half_gain"] == pytest.approx(2.0)
    assert override_model.params["derived_gain"] == pytest.approx(9.0)


def test_compile_gate_accepts_but_ignores_localparam_override_like_spectre(
    tmp_path,
):
    _write(
        tmp_path / "localparam_gain.va",
        """\
        `include "disciplines.vams"
        module localparam_gain(in, out);
            input in; output out; electrical in, out;
            localparam real scaled_gain = 1.0;
            analog begin V(out) <+ scaled_gain * V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "localparam_gain.va"
        Vin (in 0) vsource dc=1
        XDUT (in out) localparam_gain scaled_gain=4
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    model = result.simulator.models[0]
    assert model.params["scaled_gain"] == pytest.approx(1.0)
    assert "scaled_gain" not in model._given_params


def test_compile_gate_accepts_but_ignores_hierarchical_localparam_override(
    tmp_path,
):
    _write(
        tmp_path / "hier_localparam.va",
        """\
        `include "disciplines.vams"
        module child(in, out);
            input in; output out; electrical in, out;
            localparam real scaled_gain = 1.0;
            analog begin V(out) <+ scaled_gain * V(in); end
        endmodule

        module parent(in, out);
            input in; output out; electrical in, out;
            child #(.scaled_gain(4.0)) u_child(.in(in), .out(out));
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "hier_localparam.va"
        Vin (in 0) vsource dc=1
        XDUT (in out) parent
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    parent = result.simulator.models[0]
    child = parent._child_models[0]
    assert child.params["scaled_gain"] == pytest.approx(1.0)
    assert "scaled_gain" not in child._given_params


def test_compile_gate_ignores_unknown_hierarchical_parameter_like_spectre(
    tmp_path,
):
    _write(
        tmp_path / "hier_unknown_param.va",
        """\
        `include "disciplines.vams"
        module child(in, out);
            input in; output out; electrical in, out;
            parameter real gain = 1.0;
            analog begin V(out) <+ gain * V(in); end
        endmodule

        module parent(in, out);
            input in; output out; electrical in, out;
            child #(.unknown(2.0)) u_child(.in(in), .out(out));
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "hier_unknown_param.va"
        Vin (in 0) vsource dc=1
        XDUT (in out) parent
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    parent = result.simulator.models[0]
    child = parent._child_models[0]
    assert "unknown" not in child.params
    assert "unknown" not in child._given_params


def test_compile_gate_accepts_declared_instance_parameter_and_m_factor(tmp_path):
    _write(
        tmp_path / "gain_buf.va",
        """\
        `include "disciplines.vams"
        module gain_buf(in, out);
            input in; output out; electrical in, out;
            parameter real gain = 1.0;
            analog begin V(out) <+ gain * V(in); end
        endmodule
        """,
    )
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "gain_buf.va"
        Vin (in 0) vsource dc=1.0
        XGAIN (in out_gain) gain_buf gain=2
        XMULT (in out_m) gain_buf m=2
        save out_gain out_m
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.stage == "ok"


def test_compile_gate_rejects_instance_arity_mismatch(tmp_path):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "buf.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out extra) buf
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "instance_bind"
    assert any("terminal count mismatch" in diag.message for diag in result.diagnostics)


def test_compile_gate_rejects_non_ansi_combined_direction_and_discipline(tmp_path):
    _write_nonansi_combined_va(tmp_path / "bad.va")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "bad.va"
        Vdata (data 0) vsource dc=0
        Vclk (clk 0) vsource dc=0
        Vretimed_data (retimed_data 0) vsource dc=0
        XDUT (data clk retimed_data up down) nonansi_bad
        save up
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "verilog_a_compile"
    assert any(
        "use separate direction and discipline" in diag.message
        for diag in result.diagnostics
    )


def test_compile_gate_accepts_split_direction_and_discipline_decl(tmp_path):
    _basic_va(tmp_path, name="split_ok")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "split_ok.va"
        Vin (in 0) vsource dc=0.9
        XDUT (in out) split_ok
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.stage == "ok"


def test_compile_gate_classifies_unsupported_rust_lowering(tmp_path):
    _basic_va(tmp_path, name="integrator", body="V(out) <+ idt(V(in));")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "integrator.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) integrator
        save out
        """,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "rust_lowering"
    assert result.rust_report is not None
    assert not result.rust_report.supported
    assert any("no_event_transition_ir" in reason for reason in result.rust_report.reasons)


def test_compile_gate_classifies_rust_lowering_exception(tmp_path, monkeypatch):
    scs = _netlist(
        tmp_path,
        """\
        Vin (in 0) vsource dc=0.9
        save in
        """,
    )

    def fail_lowering(**_kwargs):
        raise RuntimeError("sentinel lowering failure")

    monkeypatch.setattr(
        "evas.netlist.runner.build_source_record_rust_program",
        fail_lowering,
    )

    result = compile_spectre_netlist(str(scs))

    assert not result.ok
    assert result.stage == "rust_lowering"
    assert result.rust_report is not None
    assert not result.rust_report.supported
    assert any(
        "sentinel lowering failure" in diagnostic.message
        for diagnostic in result.diagnostics
    )


def test_compile_gate_does_not_run_transient(tmp_path, monkeypatch):
    _basic_va(tmp_path, name="buf")
    scs = _netlist(
        tmp_path,
        """\
        ahdl_include "buf.va"
        Vin (in 0) vsource dc=1.0
        XDUT (in out) buf
        tran tran stop=1n maxstep=100p
        save out
        """,
    )

    def fail_run(self, *args, **kwargs):
        raise AssertionError("compile gate must not call Simulator.run()")

    monkeypatch.setattr(Simulator, "run", fail_run)

    result = compile_spectre_netlist(str(scs))

    assert result.ok
    assert result.stage == "ok"
