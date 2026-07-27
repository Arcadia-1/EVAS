import shutil
import subprocess
from pathlib import Path

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator, dc


RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"


def _build_rust_core_or_skip():
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(["cargo", "build", "--release"], cwd=RUST_CORE, check=True)


def _require_existing_rust_core_or_skip():
    suffix = ".dylib" if shutil.which("otool") is not None else ".so"
    if not (RUST_CORE / "target" / "release" / f"libevas_rust_core{suffix}").exists():
        pytest.skip("prebuilt evas rust core is not available")


def _run_last(
    src: str,
    signal: str,
    *,
    rust: bool = False,
    rust_body_ir: bool = False,
) -> tuple[float, Simulator]:
    model = compile_module(parse(src))()
    sim = Simulator()
    sim.add_model(model)
    sim.record(signal)
    result = sim.run(
        tstop=2e-9,
        tstep=1e-9,
        record_step=1e-9,
        rust_full_model_fastpath=rust,
        rust_full_model_required=rust,
        rust_required=rust,
        rust_body_ir=rust_body_ir,
        skip_source_error_control=True,
    )
    return float(result.signals[signal][-1]), sim


def test_multiple_voltage_contributions_accumulate_per_evaluate_python() -> None:
    src = """\
`include "disciplines.vams"
module multi_vcontrib(out);
    output voltage out;
    analog begin
        V(out) <+ 0.20;
        V(out) <+ 0.30;
    end
endmodule
"""
    value, _sim = _run_last(src, "out")
    assert value == pytest.approx(0.50)


def test_multiple_voltage_contributions_accumulate_per_evaluate_body_ir() -> None:
    _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"
module multi_vcontrib_rust(out);
    output voltage out;
    analog begin
        V(out) <+ 0.20;
        V(out) <+ 0.30;
    end
endmodule
"""
    value, sim = _run_last(src, "out", rust_body_ir=True)
    assert sim._perf_stats["rust_body_ir_enabled"] == 1
    assert sim._perf_stats["rust_body_ir_production_executed_total"] > 0
    assert value == pytest.approx(0.50)


def test_differential_contribution_reference_is_not_readded_when_accumulating() -> None:
    src = """\
`include "disciplines.vams"
module diff_accum(vss, outp, outn);
    inout voltage vss, outp, outn;
    analog begin
        V(outn, vss) <+ 0.20;
        V(outp, outn) <+ 0.10;
        V(outp, outn) <+ 0.20;
    end
endmodule
"""
    model = compile_module(parse(src))()
    sim = Simulator()
    sim.add_model(model)
    sim.add_source("vss", dc(0.0))
    sim.record("outp")
    sim.record("outn")
    result = sim.run(tstop=2e-9, tstep=1e-9, record_step=1e-9)

    assert result.signals["outn"][-1] == pytest.approx(0.20)
    assert result.signals["outp"][-1] == pytest.approx(0.50)


def test_source_record_differential_contributions_preserve_common_mode() -> None:
    _require_existing_rust_core_or_skip()
    src = """\
`include "disciplines.vams"
module source_record_diff_vcvs(inp, inn, outp, outn);
    inout voltage inp, inn, outp, outn;
    real vdiff;
    analog begin
        vdiff = V(inp, inn);
        V(outp, outn) <+ vdiff;
        V(outp) <+ 1.1 + vdiff / 2.0;
    end
endmodule
"""
    model = compile_module(parse(src))()
    sim = Simulator()
    sim.add_model(model)
    sim.add_source("inp", dc(0.4))
    sim.add_source("inn", dc(0.1))
    sim.record("outp")
    sim.record("outn")
    result = sim.run(
        tstop=2e-9,
        tstep=1e-9,
        record_step=1e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert sim._perf_stats["rust_sim_program_source_record_enabled"] == 1
    assert result.signals["outp"][-1] == pytest.approx(1.25)
    assert result.signals["outn"][-1] == pytest.approx(0.95)


@pytest.mark.parametrize("rust", [False, True])
def test_rtoi_truncates_toward_zero_while_integer_assignment_rounds(
    rust: bool,
) -> None:
    if rust:
        _build_rust_core_or_skip()
    src = """\
`include "disciplines.vams"
module rtoi_vs_integer_assignment(out_pos, out_neg, out_assign);
    output voltage out_pos, out_neg, out_assign;
    integer rounded;
    analog begin
        rounded = 0.731;
        V(out_pos) <+ $rtoi(0.731);
        V(out_neg) <+ $rtoi(-0.731);
        V(out_assign) <+ rounded;
    end
endmodule
"""
    model = compile_module(parse(src))()
    sim = Simulator()
    sim.add_model(model)
    for signal in ("out_pos", "out_neg", "out_assign"):
        sim.record(signal)
    result = sim.run(
        tstop=2e-9,
        tstep=1e-9,
        record_step=1e-9,
        rust_full_model_fastpath=rust,
        rust_full_model_required=rust,
        rust_required=rust,
        skip_source_error_control=True,
    )

    assert result.signals["out_pos"][-1] == pytest.approx(0.0)
    assert result.signals["out_neg"][-1] == pytest.approx(0.0)
    assert result.signals["out_assign"][-1] == pytest.approx(1.0)
