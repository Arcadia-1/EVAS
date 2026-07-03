"""Encoding helpers for continuous ``slew()`` voltage contributions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from evas.simulator.expr_ir import (
    BinaryExprIR,
    BindingTableIR,
    BodyExprOp,
    BranchAccessIR,
    FunctionCallIR,
    LiteralIR,
    UnaryExprIR,
    encode_body_expr_ops,
    static_node_ref_name,
)
from evas.simulator.stmt_ir import (
    AssignmentIR,
    BlockIR,
    ContributionIR,
    EventStatementIR,
    ForStatementIR,
    StmtIR,
    unroll_static_for_statement,
)


@dataclass(frozen=True)
class SlewContributionProgram:
    """Continuous slew contributions encoded as target/rise/fall/bias/scale ops."""

    output_node_slots: Tuple[int, ...]
    reference_node_slots: Tuple[Optional[int], ...]
    expr_segments: Tuple[Tuple[BodyExprOp, ...], ...]

    @property
    def contribution_count(self) -> int:
        return len(self.output_node_slots)


def encode_slew_contribution_program(
    stmt_ir: StmtIR,
    bindings: BindingTableIR,
    node_slots: dict[str, int],
) -> Optional[SlewContributionProgram]:
    output_slots: list[int] = []
    reference_slots: list[Optional[int]] = []
    expr_segments: list[Tuple[BodyExprOp, ...]] = []
    if not _append_slew_contribution_specs(
        stmt_ir,
        bindings,
        node_slots,
        output_slots,
        reference_slots,
        expr_segments,
    ):
        return None
    if not output_slots:
        return None
    return SlewContributionProgram(
        output_node_slots=tuple(output_slots),
        reference_node_slots=tuple(reference_slots),
        expr_segments=tuple(expr_segments),
    )


def _append_slew_contribution_specs(
    stmt_ir: StmtIR,
    bindings: BindingTableIR,
    node_slots: dict[str, int],
    output_slots: list[int],
    reference_slots: list[Optional[int]],
    expr_segments: list[Tuple[BodyExprOp, ...]],
) -> bool:
    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            if not _append_slew_contribution_specs(
                child,
                bindings,
                node_slots,
                output_slots,
                reference_slots,
                expr_segments,
            ):
                return False
        return True

    if isinstance(stmt_ir, EventStatementIR):
        return True

    if isinstance(stmt_ir, ForStatementIR):
        unrolled = unroll_static_for_statement(stmt_ir)
        if unrolled is None:
            return False
        loop_var = getattr(getattr(stmt_ir.init, "target", None), "name", None)
        if loop_var is not None:
            unrolled = BlockIR(
                tuple(
                    child
                    for child in unrolled.statements
                    if not (
                        isinstance(child, AssignmentIR)
                        and getattr(child.target, "name", None) == loop_var
                    )
                )
            )
        return _append_slew_contribution_specs(
            unrolled,
            bindings,
            node_slots,
            output_slots,
            reference_slots,
            expr_segments,
        )

    if not isinstance(stmt_ir, ContributionIR):
        return False

    target = _encode_slew_contribution_target(stmt_ir.branch, node_slots)
    if target is None:
        return False
    slew_args = _slew_call_args(stmt_ir.expr)
    if slew_args is None:
        return False

    output_slot, reference_slot = target
    encoded_segments = []
    for expr in slew_args:
        encoded = encode_body_expr_ops(expr, bindings, node_slots)
        if encoded is None:
            return False
        encoded_segments.append(encoded)

    output_slots.append(output_slot)
    reference_slots.append(reference_slot)
    expr_segments.extend(encoded_segments)
    return True


def _encode_slew_contribution_target(
    branch: BranchAccessIR,
    node_slots: dict[str, int],
) -> Optional[tuple[int, Optional[int]]]:
    if branch.access_type != "V":
        return None
    output_name = static_node_ref_name(
        branch.node1,
        branch.node1_index,
        branch.node1_index2,
    )
    if output_name is None:
        return None
    output_slot = node_slots.get(output_name)
    if output_slot is None:
        return None
    reference_slot = None
    if branch.node2 is not None:
        reference_name = static_node_ref_name(
            branch.node2,
            branch.node2_index,
            branch.node2_index2,
        )
        if reference_name is None:
            return None
        reference_slot = node_slots.get(reference_name)
        if reference_slot is None:
            return None
    return output_slot, reference_slot


def _slew_call_args(expr) -> Optional[Tuple[object, object, object, object, object]]:
    parts = _scaled_slew_parts(expr)
    if parts is None:
        return None
    target, rise, fall, output_bias, output_scale = parts
    return target, rise, fall, output_bias, output_scale


def _direct_slew_call_args(expr) -> Optional[Tuple[object, object, object]]:
    if not isinstance(expr, FunctionCallIR) or expr.name != "slew":
        return None
    if len(expr.args) > 3:
        return None
    zero = LiteralIR(0.0)
    target = expr.args[0] if len(expr.args) > 0 else zero
    rise = expr.args[1] if len(expr.args) > 1 else zero
    fall = expr.args[2] if len(expr.args) > 2 else UnaryExprIR("-", rise)
    return target, rise, fall


def _scaled_slew_parts(expr) -> Optional[Tuple[object, object, object, object, object]]:
    direct = _direct_slew_call_args(expr)
    if direct is not None:
        return (*direct, LiteralIR(0.0), LiteralIR(1.0))

    if isinstance(expr, UnaryExprIR) and expr.op == "-":
        child = _scaled_slew_parts(expr.operand)
        if child is None:
            return None
        target, rise, fall, bias, scale = child
        return target, rise, fall, UnaryExprIR("-", bias), UnaryExprIR("-", scale)

    if not isinstance(expr, BinaryExprIR):
        return None

    if expr.op in {"+", "-"}:
        left = _scaled_slew_parts(expr.left)
        right = _scaled_slew_parts(expr.right)
        if left is not None and right is not None:
            return None
        if left is not None:
            target, rise, fall, bias, scale = left
            output_bias = BinaryExprIR(expr.op, bias, expr.right)
            return target, rise, fall, output_bias, scale
        if right is not None:
            target, rise, fall, bias, scale = right
            if expr.op == "+":
                output_bias = BinaryExprIR("+", expr.left, bias)
                output_scale = scale
            else:
                output_bias = BinaryExprIR("-", expr.left, bias)
                output_scale = UnaryExprIR("-", scale)
            return target, rise, fall, output_bias, output_scale
        return None

    if expr.op == "*":
        left = _scaled_slew_parts(expr.left)
        right = _scaled_slew_parts(expr.right)
        if left is not None and right is not None:
            return None
        if left is not None:
            target, rise, fall, bias, scale = left
            return (
                target,
                rise,
                fall,
                BinaryExprIR("*", bias, expr.right),
                BinaryExprIR("*", scale, expr.right),
            )
        if right is not None:
            target, rise, fall, bias, scale = right
            return (
                target,
                rise,
                fall,
                BinaryExprIR("*", expr.left, bias),
                BinaryExprIR("*", expr.left, scale),
            )
        return None

    return None
