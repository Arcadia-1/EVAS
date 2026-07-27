"""Audit 300 tests for idtmod fourth-argument compatibility in body IR."""

from __future__ import annotations

import pytest

from evas.compiler.parser import parse
from evas.simulator.backend import compile_module
from evas.simulator.engine import Simulator
from evas.simulator.expr_ir import (
    SYMBOL_STATE_SCALAR,
    BindingTableIR,
    StateBindingIR,
    build_state_binding_ir,
)
from evas.simulator.rust_backend import BODY_STMT_IDTMOD
from evas.simulator.stmt_ir import (
    BodyStmtProgram,
    encode_body_stmt_ops,
    idtmod_hidden_state_names,
    lower_stmt,
)


def _encode_idtmod_program(call: str, node_slots: dict[str, int] | None = None):
    module = parse(
        f"""\
`include "disciplines.vams"
module idtmod_fourth_arg_sample(ctrl);
    input voltage ctrl;
    real phase = 0.0;
    analog begin
        phase = {call};
    end
endmodule
"""
    )
    stmt_ir = lower_stmt(module.analog_block.body)
    assert stmt_ir is not None
    bindings = _with_idtmod_hidden_slots(build_state_binding_ir(module), "phase")
    return encode_body_stmt_ops(stmt_ir, bindings, node_slots or {})


def _with_idtmod_hidden_slots(bindings: BindingTableIR, target_name: str) -> BindingTableIR:
    target = bindings.resolve(target_name)
    assert target is not None
    augmented = list(bindings.bindings)
    next_slot = target.slot + 1
    for hidden_name in idtmod_hidden_state_names(target_name):
        augmented.append(
            StateBindingIR(
                name=hidden_name,
                kind=SYMBOL_STATE_SCALAR,
                slot=next_slot,
                integer=False,
            )
        )
        next_slot += 1
    return BindingTableIR(tuple(augmented))


def test_idtmod_static_zero_fourth_arg_lowers_to_three_arg_body_ir():
    program = _encode_idtmod_program("idtmod(1.0, 0.0, 1.0, 0.0)")

    assert isinstance(program, BodyStmtProgram)
    assert len(program.stmt_ops) == 1
    stmt_op = program.stmt_ops[0]
    assert stmt_op.target_kind == BODY_STMT_IDTMOD
    assert stmt_op.expr_count == 3


@pytest.mark.parametrize(
    "call,node_slots",
    (
        ("idtmod(1.0, 0.0, 1.0, 1.0)", {}),
        ("idtmod(1.0, 0.0, 1.0, V(ctrl))", {"ctrl": 0}),
    ),
)
def test_idtmod_unsupported_fourth_arg_is_rejected(
    call: str,
    node_slots: dict[str, int],
):
    assert _encode_idtmod_program(call, node_slots) is None


def test_idtmod_parameter_fourth_arg_is_explicitly_not_constant_folded():
    """Parameter-valued reset remains rejected until parameter values reach body IR."""
    assert _encode_idtmod_program(
        "idtmod(1.0, 0.0, 1.0, reset)"
    ) is None


def test_empty_cross_does_not_emit_step_end_idtmod_state_at_interpolated_time():
    module = parse(
        """\
`include "disciplines.vams"
module idtmod_empty_cross(outp, metric);
    output voltage outp;
    output voltage metric;
    real phase;
    analog begin
        phase = idtmod(7.0e6, 0.0, 1.0, 0.0);
        @(cross(phase - 0.5, +1)) begin
        end
        V(outp) <+ 0.45 + 0.4 * sin(6.283185307179586 * phase);
        V(metric) <+ 0.9 * phase;
    end
endmodule
"""
    )
    model_cls = compile_module(module)
    sim = Simulator()
    sim.add_model(model_cls())
    sim.record("outp")
    sim.record("metric")

    result = sim.run(
        tstop=100e-9,
        tstep=4e-9,
        record_step=4e-9,
        rust_full_model_fastpath=True,
        rust_full_model_required=True,
        rust_required=True,
        skip_source_error_control=True,
    )

    assert len(result.time) == len(result.signals["metric"])
    for time, metric in zip(result.time, result.signals["metric"]):
        expected_phase = (7.0e6 * float(time)) % 1.0
        assert float(metric) == pytest.approx(0.9 * expected_phase, abs=1.0e-9)
