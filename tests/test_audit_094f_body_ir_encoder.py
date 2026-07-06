"""Audit 094f tests for ExprIR to Rust body-op encoding."""

from __future__ import annotations

import shutil
import subprocess
from array import array
from pathlib import Path

import pytest

from evas.compiler.ast_nodes import BranchAccess, Identifier
from evas.compiler.parser import parse
from evas.compiler.preprocessor import preprocess
from evas.simulator.backend import compile_module
from evas.simulator.expr_ir import (
    BinaryExprIR,
    BranchAccessIR,
    LoweringContext,
    build_state_binding_ir,
    encode_body_expr_ops,
    lower_expr,
)
from evas.simulator.rust_backend import (
    BODY_STMT_FILE_GETS,
    BODY_STMT_FILE_SCANF,
    BODY_STMT_WHILE,
    BODY_TARGET_STATE,
    BodyStmtOp,
    default_rust_core_library_path,
    load_rust_backend,
)
from evas.simulator.stmt_ir import (
    BodyStmtProgram,
    EventBodyProgram,
    EventStatementIR,
    StatementLoweringContext,
    encode_body_stmt_ops,
    encode_event_body_program,
    lower_stmt,
)
from evas.simulator.rust_program import _RustSimSideEffectBuilder

RUST_CORE = Path(__file__).resolve().parents[1] / "evas" / "rust_core"
PIPELINE_STAGE_VA = (
    Path(__file__).resolve().parents[2]
    / "behavioral-veriloga-eval"
    / "benchmark-vabench-release-v1"
    / "tasks"
    / "CT01_data_converter_models"
    / "vbr1_l1_pipeline_adc_stage"
    / "forms"
    / "tb"
    / "gold"
    / "pipeline_stage.va"
)


def _pipeline_stage_va() -> Path:
    if not PIPELINE_STAGE_VA.exists():
        pytest.skip(
            "pipeline_stage fixture lives in the behavioral-veriloga-eval "
            f"sibling checkout: {PIPELINE_STAGE_VA}"
        )
    return PIPELINE_STAGE_VA


SAMPLE = """\
`include "disciplines.vams"
module body_encoder_sample(vin, vref, out);
    input voltage vin, vref;
    output voltage out;
    parameter real gain = -2.0;
    parameter real thresh = 0.3;
    real acc = 0.0;
    analog begin
        acc = ((V(vin, vref) * abs(gain)) > thresh) ? acc + 1.0 : 0.0;
    end
endmodule
"""


STMT_SAMPLE = """\
`include "disciplines.vams"
module stmt_encoder_sample(vin, out);
    input voltage vin;
    output voltage out;
    parameter real gain = 2.0;
    parameter real offset = 0.1;
    real acc = 0.0;
    analog begin
        acc = V(vin) * gain;
        V(out) <+ acc + offset;
    end
endmodule
"""


def _build_rust_core():
    if shutil.which("cargo") is None:
        pytest.skip("cargo is not available")
    subprocess.run(
        ["cargo", "build", "--release"],
        cwd=RUST_CORE,
        check=True,
    )


def test_expr_ir_encodes_to_rust_body_ops_and_executes_state_write():
    _build_rust_core()
    module = parse(SAMPLE)
    assignment = module.analog_block.body.statements[0]
    expr_ir = lower_expr(assignment.value, LoweringContext.veriloga_body())
    assert expr_ir is not None
    bindings = build_state_binding_ir(module)
    acc_binding = bindings.resolve("acc")
    assert acc_binding is not None
    expr_ops = encode_body_expr_ops(expr_ir, bindings, {"vin": 0, "vref": 1})
    assert expr_ops is not None

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=[
            BodyStmtOp(
                target_kind=BODY_TARGET_STATE,
                target_id=acc_binding.slot,
                expr_start=0,
                expr_count=len(expr_ops),
            )
        ],
        expr_ops=expr_ops,
    )
    node_values = array("d", [0.5, 0.3])
    state_values = array("d", [3.5])
    param_values = array("d", [-2.0, 0.3])

    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([4.5])

    node_values = array("d", [0.4, 0.3])
    state_values = array("d", [3.5])
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([0.0])


def test_expr_ir_encoder_rejects_dynamic_indexed_voltage_read():
    module = parse(SAMPLE)
    bindings = build_state_binding_ir(module)
    dynamic_expr = lower_expr(
        BranchAccess("V", "vin", node1_index=Identifier("acc")),
        LoweringContext.veriloga_body(),
    )
    assert isinstance(dynamic_expr, BranchAccessIR)

    assert encode_body_expr_ops(dynamic_expr, bindings, {"vin": 0}) is None


def test_stmt_ir_encodes_ordered_state_and_output_writes_to_rust_batch():
    _build_rust_core()
    module = parse(STMT_SAMPLE)
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    program = encode_body_stmt_ops(stmt_ir, bindings, {"vin": 0, "out": 1})
    assert isinstance(program, BodyStmtProgram)
    assert len(program.stmt_ops) == 2

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    node_values = array("d", [0.25, 0.0])
    state_values = array("d", [0.0])
    param_values = array("d", [2.0, 0.1])

    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([0.5])
    assert node_values.tolist() == pytest.approx([0.25, 0.6])


def test_stmt_ir_unrolls_dynamic_state_array_read_write_to_rust_batch():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module dynamic_array_sample;
    integer idx = 0;
    integer src = 0;
    integer arr[0:3];
    integer out = 0;
    analog begin
        arr[idx] = src;
        out = arr[idx];
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    idx_binding = bindings.resolve("idx")
    src_binding = bindings.resolve("src")
    out_binding = bindings.resolve("out")
    arr2_binding = bindings.resolve("arr[2]")
    assert idx_binding is not None
    assert src_binding is not None
    assert out_binding is not None
    assert arr2_binding is not None
    program = encode_body_stmt_ops(stmt_ir, bindings, {})
    assert isinstance(program, BodyStmtProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    max_slot = max(binding.slot for binding in bindings.bindings)
    node_values = array("d")
    state_values = array("d", [0.0] * (max_slot + 1))
    param_values = array("d")

    state_values[idx_binding.slot] = 2.0
    state_values[src_binding.slot] = 7.0
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values[arr2_binding.slot] == pytest.approx(7.0)
    assert state_values[out_binding.slot] == pytest.approx(7.0)


def test_stmt_ir_encodes_reduction_unary_ops_to_rust_batch():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module reduction_unary_sample;
    integer code = 0;
    real metric = 0.0;
    analog begin
        metric = (^code) + (|code);
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    code_binding = bindings.resolve("code")
    metric_binding = bindings.resolve("metric")
    assert code_binding is not None
    assert metric_binding is not None
    program = encode_body_stmt_ops(stmt_ir, bindings, {})
    assert isinstance(program, BodyStmtProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    node_values = array("d")
    state_values = array(
        "d",
        [0.0] * (max(code_binding.slot, metric_binding.slot) + 1),
    )
    param_values = array("d")

    state_values[code_binding.slot] = 5.0
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values[metric_binding.slot] == pytest.approx(1.0)

    state_values[code_binding.slot] = 7.0
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values[metric_binding.slot] == pytest.approx(2.0)


def test_stmt_ir_inlines_piecewise_user_function_to_rust_batch():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module user_fn_clamp(vin, out);
    input voltage vin;
    output voltage out;
    real y;
    analog function real clamp_window;
        input x;
        real x;
        begin
            if (x < 0.1) clamp_window = 0.1;
            else if (x > 0.8) clamp_window = 0.8;
            else clamp_window = x;
        end
    endfunction
    analog begin
        y = clamp_window(V(vin));
        V(out) <+ y;
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(
        module.analog_block.body,
        StatementLoweringContext.veriloga_body(user_functions=module.functions),
    )
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    program = encode_body_stmt_ops(stmt_ir, bindings, {"vin": 0, "out": 1})
    assert isinstance(program, BodyStmtProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    node_values = array("d", [0.9, 0.0])
    state_values = array("d", [0.0])
    param_values = array("d", [])

    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([0.8])
    assert node_values.tolist() == pytest.approx([0.9, 0.8])


def test_stmt_ir_inlines_user_function_with_local_branch_state():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module user_fn_normalize(vin, out);
    input voltage vin;
    output voltage out;
    real y;
    analog function real normalize4;
        input x;
        real x;
        integer code;
        begin
            code = x < 0.0 ? 0 : (x > 0.9 ? 15 : floor(16.0 * x / 0.9));
            if (code > 15) code = 15;
            normalize4 = code / 15.0 * 0.9;
        end
    endfunction
    analog begin
        y = normalize4(V(vin));
        V(out) <+ y;
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(
        module.analog_block.body,
        StatementLoweringContext.veriloga_body(user_functions=module.functions),
    )
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    program = encode_body_stmt_ops(stmt_ir, bindings, {"vin": 0, "out": 1})
    assert isinstance(program, BodyStmtProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    node_values = array("d", [0.91, 0.0])
    state_values = array("d", [0.0])
    param_values = array("d", [])

    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([0.9])
    assert node_values.tolist() == pytest.approx([0.91, 0.9])


def test_stmt_ir_rewrites_fgets_sscanf_pair_to_file_scanf():
    module = parse(
        """\
`include "disciplines.vams"
module file_line_parse();
    parameter string filename = "config_lines.txt";
    integer fd;
    integer parsed;
    integer mode;
    string line;
    analog begin
        @(initial_step) begin
            fd = $fopen(filename, "r");
            $fgets(line, fd);
            parsed = $sscanf(line, "mode=%d", mode);
            $fclose(fd);
        end
    end
endmodule
"""
    )
    model = compile_module(module)()
    initial_body = module.analog_block.body.statements[0].body
    stmt_ir = lower_stmt(initial_body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    side_effects = _RustSimSideEffectBuilder(model)

    program = encode_body_stmt_ops(
        stmt_ir,
        bindings,
        {},
        side_effects=side_effects,
    )

    assert isinstance(program, BodyStmtProgram)
    kinds = [int(op.target_kind) for op in program.stmt_ops]
    assert BODY_STMT_FILE_SCANF in kinds
    assert BODY_STMT_FILE_GETS not in kinds
    assert [effect.kind for effect in side_effects.effects] == [
        "fopen",
        "fscanf",
        "fclose",
    ]


def test_stmt_ir_encoder_rejects_event_body_until_scheduler_owns_ordering():
    module = parse(
        """\
`include "disciplines.vams"
module event_stmt_sample(clk);
    input voltage clk;
    integer q = 0;
    analog begin
        @(cross(V(clk) - 0.5, +1)) q = q + 1;
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)

    assert encode_body_stmt_ops(stmt_ir, bindings, {"clk": 0}) is None


def test_event_body_program_encodes_cross_body_write_set_for_future_scheduler():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module event_body_sample(clk, out);
    input voltage clk;
    output voltage out;
    integer q = 0;
    analog begin
        @(cross(V(clk) - 0.5, +1)) begin
            q = q + 1;
            V(out) <+ q;
        end
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body.statements[0])
    assert isinstance(stmt_ir, EventStatementIR)
    bindings = build_state_binding_ir(module)
    program = encode_event_body_program(stmt_ir, bindings, {"clk": 0, "out": 1})
    assert isinstance(program, EventBodyProgram)
    assert len(program.body_program.stmt_ops) == 2

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.body_program.stmt_ops,
        expr_ops=program.body_program.expr_ops,
    )
    node_values = array("d", [0.0, 0.0])
    state_values = array("d", [2.0])
    param_values = array("d")

    backend.evaluate_body_ir(batch, node_values, state_values, param_values)

    assert state_values.tolist() == pytest.approx([3.0])
    assert node_values.tolist() == pytest.approx([0.0, 3.0])


def test_event_body_program_encodes_if_else_body_to_rust_batch():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module event_body_if_sample(clk);
    input voltage clk;
    integer q = 0;
    analog begin
        @(cross(V(clk) - 0.5, +1)) begin
            if (q > 0) q = 0;
            else q = 1;
        end
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body.statements[0])
    assert isinstance(stmt_ir, EventStatementIR)
    bindings = build_state_binding_ir(module)
    program = encode_event_body_program(stmt_ir, bindings, {"clk": 0})
    assert isinstance(program, EventBodyProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.body_program.stmt_ops,
        expr_ops=program.body_program.expr_ops,
    )
    node_values = array("d", [0.0])
    param_values = array("d")

    state_values = array("d", [2.0])
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values.tolist() == pytest.approx([0.0])

    state_values = array("d", [0.0])
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values.tolist() == pytest.approx([1.0])


def test_body_ir_while_loop_executes_with_guarded_rust_opcode():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module body_while_sample;
    real phase_err = 0.0;
    real ref_period = 1.0;
    analog begin
        while (phase_err > 0.5 * ref_period) phase_err = phase_err - ref_period;
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = build_state_binding_ir(module)
    program = encode_body_stmt_ops(stmt_ir, bindings, {})
    assert isinstance(program, BodyStmtProgram)
    assert any(op.target_kind == BODY_STMT_WHILE for op in program.stmt_ops)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.stmt_ops,
        expr_ops=program.expr_ops,
    )
    node_values = array("d")
    param_values = array("d")
    phase_slot = bindings.resolve("phase_err").slot
    period_slot = bindings.resolve("ref_period").slot

    state_values = array("d", [0.0, 0.0])
    state_values[phase_slot] = 3.2
    state_values[period_slot] = 1.0
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values[phase_slot] == pytest.approx(0.2)

    state_values[phase_slot] = 0.3
    backend.evaluate_body_ir(batch, node_values, state_values, param_values)
    assert state_values[phase_slot] == pytest.approx(0.3)


def test_pipeline_stage_phi2_if_else_and_clamp_event_body_executes_in_rust_batch():
    _build_rust_core()
    pipeline_stage_va = _pipeline_stage_va()
    source = pipeline_stage_va.read_text(encoding="utf-8")
    preprocessed_source, _defines, _default_transition = preprocess(
        source,
        source_dir=str(pipeline_stage_va.parent),
    )
    module = parse(preprocessed_source)
    stmt_ir = lower_stmt(module.analog_block.body.statements[2])
    assert isinstance(stmt_ir, EventStatementIR)

    bindings = build_state_binding_ir(module)
    node_slots = {name: idx for idx, name in enumerate(module.ports)}
    program = encode_event_body_program(stmt_ir, bindings, node_slots)
    assert isinstance(program, EventBodyProgram)

    backend = load_rust_backend(default_rust_core_library_path())
    batch = backend.make_body_ir_batch(
        stmt_ops=program.body_program.stmt_ops,
        expr_ops=program.body_program.expr_ops,
    )
    param_values = array("d", [0.45, 0.9, 200e-12])

    def run_phi2(vin_s: float) -> dict[str, float]:
        node_values = array("d", [0.9, 0.0, 0.0, 0.9, vin_s, 0.9, 0.0, 0.0, 0.0])
        state_values = array("d", [vin_s, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        backend.evaluate_body_ir(batch, node_values, state_values, param_values)
        return {
            name: state_values[bindings.resolve(name).slot]
            for name in (
                "vin_s",
                "vcm",
                "vin_rel",
                "vref_qtr",
                "vres_level",
                "d1_level",
                "d0_level",
            )
        }

    upper = run_phi2(0.72)
    assert upper["d1_level"] == pytest.approx(0.9)
    assert upper["d0_level"] == pytest.approx(0.0)
    assert upper["vres_level"] == pytest.approx(0.54)

    middle = run_phi2(0.45)
    assert middle["d1_level"] == pytest.approx(0.0)
    assert middle["d0_level"] == pytest.approx(0.9)
    assert middle["vres_level"] == pytest.approx(0.45)

    lower = run_phi2(0.18)
    assert lower["d1_level"] == pytest.approx(0.0)
    assert lower["d0_level"] == pytest.approx(0.0)
    assert lower["vres_level"] == pytest.approx(0.36)

    assert run_phi2(1.2)["vres_level"] == pytest.approx(0.9)
    assert run_phi2(-0.2)["vres_level"] == pytest.approx(0.0)


def test_event_trigger_expression_uses_standalone_rust_expr_eval():
    _build_rust_core()
    module = parse(
        """\
`include "disciplines.vams"
module event_trigger_expr_sample(clk);
    input voltage clk;
    integer q = 0;
    analog begin
        @(cross(V(clk) - 0.5, +1)) q = q + 1;
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body.statements[0])
    assert isinstance(stmt_ir, EventStatementIR)
    trigger_expr = stmt_ir.event.args[0]
    assert isinstance(trigger_expr, BinaryExprIR)
    bindings = build_state_binding_ir(module)
    expr_ops = encode_body_expr_ops(trigger_expr, bindings, {"clk": 0})
    assert expr_ops is not None

    backend = load_rust_backend(default_rust_core_library_path())
    value = backend.evaluate_body_expr(
        expr_ops,
        node_values=array("d", [0.7]),
        state_values=array("d", [0.0]),
        param_values=array("d"),
    )

    assert value == pytest.approx(0.2)
