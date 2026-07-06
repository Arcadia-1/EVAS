"""Typed EVAS2 Rust simulation program schema.

This module is intentionally schema-first: Python lowers supported EVAS
semantics into a typed program, and Rust owns the transient loop for programs
that are fully lowered.  Unsupported features are reported explicitly instead
of silently falling back when strict EVAS2 is requested.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple

from evas.compiler.ast_nodes import (
    ArrayAccess as AstArrayAccess,
)
from evas.compiler.ast_nodes import (
    Assignment as AstAssignment,
)
from evas.compiler.ast_nodes import (
    BinaryExpr as AstBinaryExpr,
)
from evas.compiler.ast_nodes import (
    Block as AstBlock,
)
from evas.compiler.ast_nodes import (
    BranchAccess as AstBranchAccess,
)
from evas.compiler.ast_nodes import (
    CaseItem as AstCaseItem,
)
from evas.compiler.ast_nodes import (
    CaseStatement as AstCaseStatement,
)
from evas.compiler.ast_nodes import (
    CombinedEvent as AstCombinedEvent,
)
from evas.compiler.ast_nodes import (
    Contribution as AstContribution,
)
from evas.compiler.ast_nodes import (
    EventExpr as AstEventExpr,
)
from evas.compiler.ast_nodes import (
    EventStatement as AstEventStatement,
)
from evas.compiler.ast_nodes import (
    ForStatement as AstForStatement,
)
from evas.compiler.ast_nodes import (
    FunctionCall as AstFunctionCall,
)
from evas.compiler.ast_nodes import (
    Identifier as AstIdentifier,
)
from evas.compiler.ast_nodes import (
    IfStatement as AstIfStatement,
)
from evas.compiler.ast_nodes import (
    MethodCall as AstMethodCall,
)
from evas.compiler.ast_nodes import (
    NumberLiteral as AstNumberLiteral,
)
from evas.compiler.ast_nodes import ParamType
from evas.compiler.ast_nodes import (
    StringLiteral as AstStringLiteral,
)
from evas.compiler.ast_nodes import (
    SystemTask as AstSystemTask,
)
from evas.compiler.ast_nodes import (
    TaskCall as AstTaskCall,
)
from evas.compiler.ast_nodes import (
    TernaryExpr as AstTernaryExpr,
)
from evas.compiler.ast_nodes import (
    UnaryExpr as AstUnaryExpr,
)
from evas.compiler.ast_nodes import (
    WhileStatement as AstWhileStatement,
)
from evas.simulator.evaluate_ir import (
    SOURCE_NODE,
    SOURCE_STATE,
    TARGET_NODE,
    TARGET_STATE,
    normalize_linear_ops,
)
from evas.simulator.expr_ir import (
    SYMBOL_PORT,
    SYMBOL_STATE_ARRAY,
    SYMBOL_STATE_SCALAR,
    ArrayAccessIR,
    BinaryExprIR,
    BindingTableIR,
    BranchAccessIR,
    ExprIR,
    FunctionCallIR,
    IdentifierIR,
    LiteralIR,
    MethodCallIR,
    StateBindingIR,
    TernaryExprIR,
    UnaryExprIR,
    build_state_binding_ir,
    static_array_element_name,
    static_node_ref_name,
)
from evas.simulator.rust_backend import (
    BODY_EXPR_READ_NODE,
    BODY_EXPR_READ_PARAM,
    BODY_EXPR_READ_STATE,
    BODY_STMT_FILE_CLOSE,
    BODY_STMT_FILE_GETS,
    BODY_STMT_FILE_OPEN,
    BODY_STMT_FILE_SCANF,
    BODY_STMT_FILE_SEEK,
    BODY_STMT_FILE_TELL,
    BODY_STMT_FILE_WRITE,
    BODY_STMT_STRING_WRITE,
    BODY_STMT_STROBE,
    BODY_TARGET_NODE,
    BODY_TARGET_STATE,
    BodyExprOp,
    BodyStmtOp,
)
from evas.simulator.schedule_ir import (
    EVENT_DUE_ABOVE,
    EVENT_DUE_CROSS,
    CombinedEventIR,
    EventIR,
    EventTriggerIR,
    encode_event_due_program,
)
from evas.simulator.slew_runtime import encode_slew_contribution_program
from evas.simulator.stmt_ir import (
    AssignmentIR,
    BlockIR,
    CaseItemIR,
    CaseStatementIR,
    ContributionIR,
    EventStatementIR,
    ForStatementIR,
    IfStatementIR,
    SystemTaskIR,
    WhileStatementIR,
    classify_body_stmt_ops_rejection,
    ddt_hidden_state_names,
    encode_body_stmt_ops,
    idt_hidden_state_names,
    idtmod_hidden_state_names,
    last_crossing_hidden_state_names,
    lower_stmt,
    unroll_static_for_statement,
)
from evas.simulator.transition_runtime import encode_transition_contribution_program

SOURCE_DC = "dc"
SOURCE_PULSE = "pulse"
SOURCE_SQUARE = "square"
SOURCE_SINE = "sine"
SOURCE_PWL = "pwl"
EVENT_DUE_ALWAYS = "always"
EVENT_PHASE_PRE = 0
EVENT_PHASE_POST = 1
_INITIAL_FILE_READ_FUNCTIONS = frozenset(
    {"$feof", "$fgets", "$fscanf", "$fseek", "$ftell", "$rewind"}
)
_INITIAL_FILE_READ_TASKS = frozenset({"$fgets", "$fscanf", "$fseek", "$rewind"})


@dataclass(frozen=True)
class RustSimNode:
    """A voltage-domain node slot owned by the Rust loop."""

    name: str
    node_id: int
    initial_value: float = 0.0


@dataclass(frozen=True)
class RustSimState:
    """A model state slot owned by the Rust loop."""

    name: str
    state_id: int
    initial_value: float = 0.0
    is_integer: bool = False


@dataclass(frozen=True)
class RustSimParam:
    """A model parameter slot used by Rust evaluate/event opcodes."""

    name: str
    param_id: int
    value: float


@dataclass(frozen=True)
class RustSimSource:
    """A source opcode with fixed node ownership and typed waveform payload."""

    node: str
    node_id: int
    kind: str
    params: Tuple[float, ...]
    data_start: int = 0
    data_count: int = 0
    flags: int = 0


@dataclass(frozen=True)
class RustSimEvent:
    """An event detector entry such as initial_step, timer, cross, or above."""

    kind: str
    event_id: int
    phase: int = EVENT_PHASE_PRE
    direction: int = 0
    expr_start: int = 0
    expr_count: int = 0
    time_tol_start: int = 0
    time_tol_count: int = 0
    expr_tol_start: int = 0
    expr_tol_count: int = 0
    timer_start_expr_start: int = 0
    timer_start_expr_count: int = 0
    timer_period_expr_start: int = 0
    timer_period_expr_count: int = 0
    body_stmt_start: int = 0
    body_stmt_count: int = 0


@dataclass(frozen=True)
class RustSimBodyOp:
    """A lowered event/evaluate body operation."""

    op_kind: str
    target_kind: str
    target_id: int
    args: Tuple[float, ...] = ()
    source_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class RustSimLinearTerm:
    """One linear source term in a continuous evaluate opcode."""

    source_kind: int
    source_id: int
    gain: float


@dataclass(frozen=True)
class RustSimLinearCondition:
    """A conditional select for a continuous linear write."""

    op_kind: int
    left_bias: float
    left_terms: Tuple[RustSimLinearTerm, ...]
    right_bias: float
    right_terms: Tuple[RustSimLinearTerm, ...]


@dataclass(frozen=True)
class RustSimLinearOp:
    """A continuous evaluate write executed by the Rust loop."""

    target_kind: int
    target_id: int
    bias: float
    terms: Tuple[RustSimLinearTerm, ...]
    condition: Optional[RustSimLinearCondition] = None
    false_bias: float = 0.0
    false_terms: Tuple[RustSimLinearTerm, ...] = ()
    target_integer: bool = False


@dataclass(frozen=True)
class RustSimZiNdOp:
    """A continuous sampled-data zi_nd voltage write executed by Rust."""

    target_node_id: int
    input_node_id: int
    num_start: int
    num_count: int
    den_start: int
    den_count: int
    interval: float


@dataclass(frozen=True)
class RustSimBranchIdtOp:
    """A branch-current idt() voltage contribution executed by Rust."""

    target_node_id: int
    reference_node_id: Optional[int]
    input_node_id: int
    state_id: int
    gain: float
    ic: float


@dataclass(frozen=True)
class RustSimBranchDdtOp:
    """A branch-voltage ddt() current contribution executed by Rust."""

    current_node_id: int
    pos_node_id: int
    neg_node_id: int
    state_id: int
    gain: float


@dataclass(frozen=True)
class RustSimIndirectBranchOdeOp:
    """A first-order indirect-branch ddt() balance executed by Rust."""

    target_node_id: int
    reference_node_id: Optional[int]
    input_node_id: int
    state_id: int
    tau: float
    ic: float


@dataclass(frozen=True)
class RustSimTransition:
    """A transition() state slot and its output target."""

    transition_id: int
    output_node_id: int
    reference_node_id: Optional[int]
    target_expr_start: int
    target_expr_count: int
    delay_expr_start: int
    delay_expr_count: int
    rise_expr_start: int
    rise_expr_count: int
    fall_expr_start: int
    fall_expr_count: int
    output_bias_expr_start: int = 0
    output_bias_expr_count: int = 0
    output_scale_expr_start: int = 0
    output_scale_expr_count: int = 0
    default_transition: float = 1.0e-12


@dataclass(frozen=True)
class RustSimSlew:
    """A slew() state slot and its output target."""

    slew_id: int
    output_node_id: int
    reference_node_id: Optional[int]
    target_expr_start: int
    target_expr_count: int
    rise_expr_start: int
    rise_expr_count: int
    fall_expr_start: int
    fall_expr_count: int
    output_bias_expr_start: int = 0
    output_bias_expr_count: int = 0
    output_scale_expr_start: int = 0
    output_scale_expr_count: int = 0


@dataclass(frozen=True)
class RustSimRecord:
    """A recorded waveform column."""

    name: str
    node_id: int


@dataclass(frozen=True)
class RustSimStringArg:
    """A string-valued format argument replayed on the Python side."""

    kind: str
    value: str


@dataclass(frozen=True)
class RustSimSideEffect:
    """Python-owned metadata for Rust-triggered file side effects."""

    kind: str
    filename: str = ""
    mode: str = "w"
    fmt: str = ""
    target_ids: Tuple[int, ...] = ()
    target_integers: Tuple[bool, ...] = ()
    append_newline: bool = True
    target: str = ""
    format_args: Tuple[Any, ...] = ()
    owner: Any = None


@dataclass(frozen=True)
class RustSimProgram:
    """Complete typed program accepted by the strict Rust EVAS2 loop."""

    nodes: Tuple[RustSimNode, ...]
    states: Tuple[RustSimState, ...]
    params: Tuple[RustSimParam, ...]
    sources: Tuple[RustSimSource, ...]
    events: Tuple[RustSimEvent, ...]
    body_ops: Tuple[RustSimBodyOp, ...]
    transitions: Tuple[RustSimTransition, ...]
    slews: Tuple[RustSimSlew, ...]
    records: Tuple[RustSimRecord, ...]
    side_effects: Tuple[RustSimSideEffect, ...] = ()
    continuous_linear_ops: Tuple[RustSimLinearOp, ...] = ()
    zi_nd_ops: Tuple[RustSimZiNdOp, ...] = ()
    branch_idt_ops: Tuple[RustSimBranchIdtOp, ...] = ()
    branch_ddt_ops: Tuple[RustSimBranchDdtOp, ...] = ()
    indirect_branch_ode_ops: Tuple[RustSimIndirectBranchOdeOp, ...] = ()
    body_stmt_ops: Tuple[BodyStmtOp, ...] = ()
    body_expr_ops: Tuple[BodyExprOp, ...] = ()
    source_data: Tuple[float, ...] = ()
    bound_step_ops: Tuple[Any, ...] = ()
    final_step_ops: Tuple[RustSimBodyOp, ...] = ()

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def node_names(self) -> Tuple[str, ...]:
        return tuple(node.name for node in self.nodes)

    @property
    def record_names(self) -> Tuple[str, ...]:
        return tuple(record.name for record in self.records)

    @property
    def record_node_ids(self) -> Tuple[int, ...]:
        return tuple(record.node_id for record in self.records)


@dataclass(frozen=True)
class RustSimCompileReport:
    """Result of lowering Python EVAS objects into a RustSimProgram."""

    program: Optional[RustSimProgram]
    supported: bool
    reasons: Tuple[str, ...] = ()


def _waveform_metadata(waveform: Any) -> Optional[Mapping[str, Any]]:
    meta = getattr(waveform, "_evas_waveform", None)
    return meta if isinstance(meta, Mapping) else None


def _add_node(name: str, node_ids: dict[str, int], nodes: list[RustSimNode]) -> int:
    if name not in node_ids:
        node_id = len(node_ids)
        node_ids[name] = node_id
        nodes.append(RustSimNode(name=name, node_id=node_id))
    return node_ids[name]


def _model_state_names(model: Any) -> Tuple[str, ...]:
    model_cls = getattr(model, "__class__", type(model))
    names = [str(name) for name in tuple(getattr(model_cls, "_state_scalar_names", ()) or ())]
    slot_name_fn = getattr(model, "_state_array_slot_name", None)
    for array_name, lo, hi, _integer in (
        tuple(getattr(model_cls, "_state_array_ranges", ()) or ())
    ):
        for idx in range(int(lo), int(hi) + 1):
            if slot_name_fn is not None:
                names.append(str(slot_name_fn(str(array_name), idx)))
            else:
                names.append(f"{array_name}[{idx}]")
    return tuple(names)


def _model_integer_state_names(model: Any) -> set[str]:
    model_cls = getattr(model, "__class__", type(model))
    names = set(getattr(model_cls, "_integer_state_names", ()) or ())
    slot_name_fn = getattr(model, "_state_array_slot_name", None)
    for array_name, lo, hi, integer in (
        tuple(getattr(model_cls, "_state_array_ranges", ()) or ())
    ):
        if not bool(integer):
            continue
        for idx in range(int(lo), int(hi) + 1):
            if slot_name_fn is not None:
                names.add(str(slot_name_fn(str(array_name), idx)))
            else:
                names.add(f"{array_name}[{idx}]")
    return names


def _state_array_slot_ref(model: Any, state_name: str) -> Optional[tuple[str, int]]:
    slot_ref_fn = getattr(model, "_state_array_slot_ref", None)
    if slot_ref_fn is not None:
        try:
            ref = slot_ref_fn(state_name)
        except Exception:
            ref = None
        if ref is not None:
            array_name, idx = ref
            return str(array_name), int(idx)
    if not isinstance(state_name, str) or "[" not in state_name or not state_name.endswith("]"):
        return None
    array_name, raw_idx = state_name[:-1].rsplit("[", 1)
    try:
        return str(array_name), int(raw_idx)
    except ValueError:
        return None


def _model_state_value(model: Any, state_name: str) -> float:
    state_values = getattr(model, "state", {}) or {}
    if state_name in state_values:
        return float(state_values.get(state_name, 0.0))
    array_ref = _state_array_slot_ref(model, state_name)
    if array_ref is not None:
        array_name, idx = array_ref
        return float((getattr(model, "arrays", {}) or {}).get(array_name, {}).get(idx, 0.0))
    return 0.0


def _model_state_is_numeric(model: Any, state_name: str) -> bool:
    try:
        _model_state_value(model, state_name)
    except (TypeError, ValueError):
        return False
    return True


def _binding_array_slot_name(name: str, idx: int) -> str:
    return f"{name}[{int(idx)}]"


def _static_int_expr_value(expr_ir: ExprIR) -> Optional[int]:
    if isinstance(expr_ir, LiteralIR):
        if not isinstance(expr_ir.value, (int, float)):
            return None
        idx = int(expr_ir.value)
        if float(expr_ir.value) != float(idx):
            return None
        return idx
    if isinstance(expr_ir, UnaryExprIR):
        value = _static_int_expr_value(expr_ir.operand)
        if value is None:
            return None
        if expr_ir.op == "+":
            return value
        if expr_ir.op == "-":
            return -value
    return None


def _iter_static_state_array_accesses_expr(expr_ir: ExprIR):
    if isinstance(expr_ir, ArrayAccessIR):
        idx = _static_int_expr_value(expr_ir.index)
        if idx is not None:
            yield str(expr_ir.name), idx
        yield from _iter_static_state_array_accesses_expr(expr_ir.index)
        return
    if isinstance(expr_ir, BranchAccessIR):
        for child in (
            expr_ir.node1_index,
            expr_ir.node1_index2,
            expr_ir.node2_index,
            expr_ir.node2_index2,
        ):
            if child is not None:
                yield from _iter_static_state_array_accesses_expr(child)
        return
    if isinstance(expr_ir, BinaryExprIR):
        yield from _iter_static_state_array_accesses_expr(expr_ir.left)
        yield from _iter_static_state_array_accesses_expr(expr_ir.right)
        return
    if isinstance(expr_ir, UnaryExprIR):
        yield from _iter_static_state_array_accesses_expr(expr_ir.operand)
        return
    if isinstance(expr_ir, TernaryExprIR):
        yield from _iter_static_state_array_accesses_expr(expr_ir.cond)
        yield from _iter_static_state_array_accesses_expr(expr_ir.true_expr)
        yield from _iter_static_state_array_accesses_expr(expr_ir.false_expr)
        return
    if isinstance(expr_ir, FunctionCallIR):
        for arg in expr_ir.args:
            yield from _iter_static_state_array_accesses_expr(arg)


def _iter_static_state_array_accesses_event(event_ir: EventIR):
    if isinstance(event_ir, CombinedEventIR):
        for child in event_ir.events:
            yield from _iter_static_state_array_accesses_event(child)
        return
    if not isinstance(event_ir, EventTriggerIR):
        return
    for expr in event_ir.args:
        yield from _iter_static_state_array_accesses_expr(expr)
    for expr in (event_ir.time_tol, event_ir.expr_tol):
        if expr is not None:
            yield from _iter_static_state_array_accesses_expr(expr)


def _iter_static_state_array_accesses_stmt(stmt_ir: object):
    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            yield from _iter_static_state_array_accesses_stmt(child)
        return
    if isinstance(stmt_ir, AssignmentIR):
        if isinstance(stmt_ir.target, ArrayAccessIR):
            idx = _static_int_expr_value(stmt_ir.target.index)
            if idx is not None:
                yield str(stmt_ir.target.name), idx
            yield from _iter_static_state_array_accesses_expr(stmt_ir.target.index)
        yield from _iter_static_state_array_accesses_expr(stmt_ir.value)
        return
    if isinstance(stmt_ir, ContributionIR):
        yield from _iter_static_state_array_accesses_expr(stmt_ir.branch)
        yield from _iter_static_state_array_accesses_expr(stmt_ir.expr)
        return
    if isinstance(stmt_ir, EventStatementIR):
        yield from _iter_static_state_array_accesses_event(stmt_ir.event)
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, IfStatementIR):
        yield from _iter_static_state_array_accesses_expr(stmt_ir.cond)
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.then_body)
        if stmt_ir.else_body is not None:
            yield from _iter_static_state_array_accesses_stmt(stmt_ir.else_body)
        return
    if isinstance(stmt_ir, ForStatementIR):
        unrolled = unroll_static_for_statement(stmt_ir)
        if unrolled is not None:
            yield from _iter_static_state_array_accesses_stmt(unrolled)
            return
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.init)
        yield from _iter_static_state_array_accesses_expr(stmt_ir.cond)
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.update)
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, WhileStatementIR):
        yield from _iter_static_state_array_accesses_expr(stmt_ir.cond)
        yield from _iter_static_state_array_accesses_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, CaseStatementIR):
        yield from _iter_static_state_array_accesses_expr(stmt_ir.expr)
        for item in stmt_ir.items:
            for value in item.values:
                yield from _iter_static_state_array_accesses_expr(value)
            yield from _iter_static_state_array_accesses_stmt(item.body)
        return
    if isinstance(stmt_ir, SystemTaskIR):
        for arg in stmt_ir.args:
            yield from _iter_static_state_array_accesses_expr(arg)


def _extend_bindings_from_static_array_accesses(
    bindings: BindingTableIR,
    stmt_ir: object,
) -> BindingTableIR:
    accesses: dict[str, set[int]] = {}
    for name, idx in _iter_static_state_array_accesses_stmt(stmt_ir):
        accesses.setdefault(str(name), set()).add(int(idx))
    if not accesses:
        return bindings

    by_name = {binding.name: binding for binding in bindings.bindings}
    next_scalar_slot = (
        max(
            (
                int(binding.slot)
                for binding in bindings.bindings
                if binding.kind == SYMBOL_STATE_SCALAR
            ),
            default=-1,
        )
        + 1
    )
    rewritten: list[StateBindingIR] = []
    for binding in bindings.bindings:
        if binding.kind == SYMBOL_STATE_ARRAY and binding.name in accesses:
            indices = set(accesses[binding.name])
            if binding.lo is not None:
                indices.add(int(binding.lo))
            if binding.hi is not None:
                indices.add(int(binding.hi))
            binding = StateBindingIR(
                name=binding.name,
                kind=binding.kind,
                slot=binding.slot,
                integer=binding.integer,
                lo=min(indices),
                hi=max(indices),
            )
        rewritten.append(binding)

    present = {binding.name for binding in rewritten}
    for name in sorted(accesses):
        array_binding = by_name.get(name)
        if array_binding is None or array_binding.kind != SYMBOL_STATE_ARRAY:
            continue
        for idx in sorted(accesses[name]):
            slot_name = _binding_array_slot_name(name, idx)
            if slot_name in present:
                continue
            rewritten.append(
                StateBindingIR(
                    name=slot_name,
                    kind=SYMBOL_STATE_SCALAR,
                    slot=next_scalar_slot,
                    integer=array_binding.integer,
                    lo=idx,
                    hi=idx,
                )
            )
            present.add(slot_name)
            next_scalar_slot += 1
    return BindingTableIR(tuple(rewritten))


def _iter_repeat_hidden_names_from_expr(expr_ir: ExprIR) -> Iterable[str]:
    if isinstance(expr_ir, IdentifierIR):
        name = str(expr_ir.name)
        if name.startswith("__evas_repeat_"):
            yield name
        return
    if isinstance(expr_ir, ArrayAccessIR):
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.index)
        return
    if isinstance(expr_ir, BinaryExprIR):
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.left)
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.right)
        return
    if isinstance(expr_ir, UnaryExprIR):
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.operand)
        return
    if isinstance(expr_ir, TernaryExprIR):
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.cond)
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.true_expr)
        yield from _iter_repeat_hidden_names_from_expr(expr_ir.false_expr)
        return
    if isinstance(expr_ir, FunctionCallIR):
        for arg in expr_ir.args:
            yield from _iter_repeat_hidden_names_from_expr(arg)
        return
    if isinstance(expr_ir, BranchAccessIR):
        for child in (
            expr_ir.node1_index,
            expr_ir.node2_index,
            expr_ir.node1_index2,
            expr_ir.node2_index2,
        ):
            if child is not None:
                yield from _iter_repeat_hidden_names_from_expr(child)


def _iter_repeat_hidden_names_from_stmt(stmt_ir: object) -> Iterable[str]:
    if isinstance(stmt_ir, AssignmentIR):
        if isinstance(stmt_ir.target, IdentifierIR):
            name = str(stmt_ir.target.name)
            if name.startswith("__evas_repeat_"):
                yield name
        elif isinstance(stmt_ir.target, ArrayAccessIR):
            yield from _iter_repeat_hidden_names_from_expr(stmt_ir.target.index)
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.value)
        return
    if isinstance(stmt_ir, ContributionIR):
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.branch)
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.expr)
        return
    if isinstance(stmt_ir, EventStatementIR):
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            yield from _iter_repeat_hidden_names_from_stmt(child)
        return
    if isinstance(stmt_ir, IfStatementIR):
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.cond)
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.then_body)
        if stmt_ir.else_body is not None:
            yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.else_body)
        return
    if isinstance(stmt_ir, ForStatementIR):
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.init)
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.cond)
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.update)
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, WhileStatementIR):
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.cond)
        yield from _iter_repeat_hidden_names_from_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, CaseStatementIR):
        yield from _iter_repeat_hidden_names_from_expr(stmt_ir.expr)
        for item in stmt_ir.items:
            for value in item.values:
                yield from _iter_repeat_hidden_names_from_expr(value)
            yield from _iter_repeat_hidden_names_from_stmt(item.body)
        return
    if isinstance(stmt_ir, SystemTaskIR):
        for arg in stmt_ir.args:
            yield from _iter_repeat_hidden_names_from_expr(arg)


def _extend_bindings_with_repeat_loop_slots(
    bindings: BindingTableIR,
    stmt_ir: object,
) -> BindingTableIR:
    names = sorted(set(_iter_repeat_hidden_names_from_stmt(stmt_ir)))
    if not names:
        return bindings
    present = {binding.name for binding in bindings.bindings}
    missing = [name for name in names if name not in present]
    if not missing:
        return bindings
    next_scalar_slot = (
        max(
            (
                int(binding.slot)
                for binding in bindings.bindings
                if binding.kind == SYMBOL_STATE_SCALAR
            ),
            default=-1,
        )
        + 1
    )
    rewritten = list(bindings.bindings)
    for name in missing:
        rewritten.append(
            StateBindingIR(
                name=name,
                kind=SYMBOL_STATE_SCALAR,
                slot=next_scalar_slot,
                integer=True,
            )
        )
        next_scalar_slot += 1
    return BindingTableIR(tuple(rewritten))


def _extend_bindings_with_stateful_function_slots(
    bindings: BindingTableIR,
    stmt_ir: object,
) -> BindingTableIR:
    ddt_targets = set(_iter_stateful_assignment_target_names(stmt_ir, bindings, "ddt"))
    idt_targets = set(_iter_stateful_assignment_target_names(stmt_ir, bindings, "idt"))
    idtmod_targets = set(_iter_stateful_assignment_target_names(stmt_ir, bindings, "idtmod"))
    last_crossing_targets = set(
        _iter_stateful_assignment_target_names(stmt_ir, bindings, "last_crossing")
    )
    if (
        not ddt_targets
        and not idt_targets
        and not idtmod_targets
        and not last_crossing_targets
    ):
        return bindings

    rewritten: list[StateBindingIR] = []
    next_scalar_slot = 0
    for binding in bindings.bindings:
        if binding.kind != SYMBOL_STATE_SCALAR:
            rewritten.append(binding)
            continue

        state_binding = StateBindingIR(
            name=binding.name,
            kind=binding.kind,
            slot=next_scalar_slot,
            integer=binding.integer,
            lo=binding.lo,
            hi=binding.hi,
        )
        rewritten.append(state_binding)
        next_scalar_slot += 1

        hidden_names: list[str] = []
        if binding.name in ddt_targets:
            hidden_names.extend(ddt_hidden_state_names(binding.name))
        if binding.name in idt_targets:
            hidden_names.extend(idt_hidden_state_names(binding.name))
        if binding.name in idtmod_targets:
            hidden_names.extend(idtmod_hidden_state_names(binding.name))
        if binding.name in last_crossing_targets:
            hidden_names.extend(last_crossing_hidden_state_names(binding.name))
        for hidden_name in hidden_names:
            rewritten.append(
                StateBindingIR(
                    name=hidden_name,
                    kind=SYMBOL_STATE_SCALAR,
                    slot=next_scalar_slot,
                    integer=False,
                )
            )
            next_scalar_slot += 1
    return BindingTableIR(tuple(rewritten))


def _iter_stateful_assignment_target_names(
    stmt_ir: object,
    bindings: BindingTableIR,
    function_name: str,
) -> Iterable[str]:
    if isinstance(stmt_ir, AssignmentIR):
        if (
            isinstance(stmt_ir.value, FunctionCallIR)
            and str(stmt_ir.value.name) == function_name
        ):
            target_name = _assignment_target_name_for_bindings(stmt_ir.target, bindings)
            if target_name is not None:
                yield target_name
        return
    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            yield from _iter_stateful_assignment_target_names(child, bindings, function_name)
        return
    if isinstance(stmt_ir, EventStatementIR):
        yield from _iter_stateful_assignment_target_names(stmt_ir.body, bindings, function_name)
        return
    if isinstance(stmt_ir, IfStatementIR):
        yield from _iter_stateful_assignment_target_names(stmt_ir.then_body, bindings, function_name)
        if stmt_ir.else_body is not None:
            yield from _iter_stateful_assignment_target_names(
                stmt_ir.else_body, bindings, function_name
            )
        return
    if isinstance(stmt_ir, (ForStatementIR, WhileStatementIR)):
        yield from _iter_stateful_assignment_target_names(stmt_ir.body, bindings, function_name)
        return
    if isinstance(stmt_ir, CaseStatementIR):
        for item in stmt_ir.items:
            yield from _iter_stateful_assignment_target_names(item.body, bindings, function_name)


def _assignment_target_name_for_bindings(
    target: object,
    bindings: BindingTableIR,
) -> Optional[str]:
    if isinstance(target, IdentifierIR):
        return target.name
    if isinstance(target, ArrayAccessIR):
        return static_array_element_name(target, bindings)
    return None


def _model_params(model: Any) -> Tuple[tuple[int, str, float], ...]:
    module = getattr(getattr(model, "__class__", type(model)), "_module_ast", None)
    params = getattr(module, "parameters", ()) or ()
    values = getattr(model, "params", {}) or {}
    result: list[tuple[int, str, float]] = []
    for local_slot, param in enumerate(params):
        name = str(getattr(param, "name", ""))
        if not name:
            continue
        try:
            value = float(values.get(name, 0.0))
        except (TypeError, ValueError):
            continue
        result.append((local_slot, name, value))
    return tuple(result)


class _RustSimSideEffectBuilder:
    def __init__(self, model: Any):
        self._model = model
        self.effects: list[RustSimSideEffect] = []
        self._string_state_names = self._collect_string_state_names(model)

    def resolve_string(self, expr_ir: ExprIR) -> Optional[str]:
        if isinstance(expr_ir, LiteralIR) and isinstance(expr_ir.value, str):
            return str(expr_ir.value)
        if isinstance(expr_ir, IdentifierIR):
            value = (getattr(self._model, "params", {}) or {}).get(expr_ir.name)
            if isinstance(value, str):
                return value
        return None

    def string_target_name(self, expr_ir: ExprIR) -> Optional[str]:
        if not isinstance(expr_ir, IdentifierIR):
            return None
        name = str(expr_ir.name)
        if name in self._string_state_names:
            return name
        value = (getattr(self._model, "state", {}) or {}).get(name)
        return name if isinstance(value, str) else None

    def string_arg(self, expr_ir: ExprIR) -> Optional[RustSimStringArg]:
        if isinstance(expr_ir, LiteralIR) and isinstance(expr_ir.value, str):
            return RustSimStringArg(kind="literal", value=str(expr_ir.value))
        if isinstance(expr_ir, IdentifierIR):
            name = str(expr_ir.name)
            value = (getattr(self._model, "params", {}) or {}).get(name)
            if isinstance(value, str):
                return RustSimStringArg(kind="literal", value=value)
            if name in self._string_state_names:
                return RustSimStringArg(kind="state", value=name)
            state_value = (getattr(self._model, "state", {}) or {}).get(name)
            if isinstance(state_value, str):
                return RustSimStringArg(kind="state", value=name)
        return None

    def add_file_open(self, filename: str, mode: str) -> int:
        return self._append(RustSimSideEffect(kind="fopen", filename=filename, mode=mode))

    def add_file_write(
        self,
        fmt: str,
        *,
        append_newline: bool = True,
        format_args: Tuple[Any, ...] = (),
    ) -> int:
        return self._append(
            RustSimSideEffect(
                kind="fwrite",
                fmt=fmt,
                append_newline=bool(append_newline),
                format_args=tuple(format_args),
                owner=self._model,
            )
        )

    def add_file_close(self) -> int:
        return self._append(RustSimSideEffect(kind="fclose"))

    def add_file_scanf(
        self,
        fmt: str,
        target_ids: tuple[int, ...],
        target_integers: tuple[bool, ...],
    ) -> int:
        return self._append(
            RustSimSideEffect(
                kind="fscanf",
                fmt=fmt,
                target_ids=target_ids,
                target_integers=target_integers,
            )
        )

    def add_file_gets(self, target_ids: tuple[int, ...] = ()) -> int:
        return self._append(RustSimSideEffect(kind="fgets", target_ids=target_ids))

    def add_file_tell(self) -> int:
        return self._append(RustSimSideEffect(kind="ftell"))

    def add_file_seek(self) -> int:
        return self._append(RustSimSideEffect(kind="fseek"))

    def add_strobe(self, fmt: str, *, format_args: Tuple[Any, ...] = ()) -> int:
        return self._append(
            RustSimSideEffect(
                kind="strobe",
                fmt=fmt,
                format_args=tuple(format_args),
                owner=self._model,
            )
        )

    def add_string_write(
        self,
        target: str,
        fmt: str,
        *,
        format_args: Tuple[Any, ...] = (),
    ) -> int:
        return self._append(
            RustSimSideEffect(
                kind="swrite",
                target=str(target),
                fmt=fmt,
                format_args=tuple(format_args),
                owner=self._model,
            )
        )

    def _append(self, effect: RustSimSideEffect) -> int:
        self.effects.append(effect)
        return len(self.effects) - 1

    @staticmethod
    def _collect_string_state_names(model: Any) -> frozenset[str]:
        module = getattr(getattr(model, "__class__", type(model)), "_module_ast", None)
        names = set()
        for variable in getattr(module, "variables", ()) or ():
            if getattr(variable, "var_type", None) == ParamType.STRING:
                names.add(str(getattr(variable, "name", "")))
        for name, value in (getattr(model, "state", {}) or {}).items():
            if isinstance(value, str):
                names.add(str(name))
        return frozenset(name for name in names if name)


def _external_node(model: Any, local_name: str) -> str:
    if str(local_name).startswith("@I:"):
        parts = str(local_name).split(":", 2)
        if len(parts) == 3:
            return _branch_current_node_name(
                _external_node(model, parts[1]),
                _external_node(model, parts[2]),
            )
    node_map = getattr(model, "node_map", {}) or {}
    if local_name in node_map:
        ext = str(node_map[local_name])
        if ext.startswith("@parent:"):
            parent = getattr(model, "_parent_model", None)
            if parent is not None:
                return _external_node(parent, ext[len("@parent:") :])
        return ext
    local_folded = str(local_name).casefold()
    for key, value in node_map.items():
        if str(key).casefold() == local_folded:
            ext = str(value)
            if ext.startswith("@parent:"):
                parent = getattr(model, "_parent_model", None)
                if parent is not None:
                    return _external_node(parent, ext[len("@parent:") :])
            return ext
    return str(local_name)


def _branch_current_node_name(node1: str, node2: str) -> str:
    return f"@I:{node1}:{node2}"


def _scalar(model: Any, value: Any) -> tuple[Optional[float], Optional[str]]:
    scalar_eval = getattr(model, "_evaluate_rust_static_affine_scalar", None)
    if scalar_eval is not None:
        try:
            return float(scalar_eval(value, getattr(model, "params", {}) or {})), None
        except Exception as exc:
            return None, f"scalar_eval_failed:{exc}"
    try:
        return float(value), None
    except Exception:
        return None, f"non_constant_scalar:{value!r}"


def _reject_model_dynamic_semantics(model: Any, model_index: int) -> Tuple[str, ...]:
    model_cls = getattr(model, "__class__", type(model))
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    reasons: list[str] = []
    has_dynamic = getattr(model, "_has_dynamic_breakpoints_tree", None)
    if has_dynamic is not None and bool(has_dynamic()):
        reasons.append(f"{prefix}:event_breakpoints_not_lowered")
    if bool(getattr(model_cls, "_has_post_update_events", True)):
        reasons.append(f"{prefix}:post_update_not_lowered")
    if tuple(getattr(model_cls, "_event_static_linear_ir_ops", ()) or ()):
        reasons.append(f"{prefix}:event_body_not_lowered")
    if tuple(getattr(model_cls, "_event_timer_static_linear_ir_ops", ()) or ()):
        reasons.append(f"{prefix}:timer_event_not_lowered")
    if tuple(getattr(model_cls, "_transition_target_ir_ops", ()) or ()):
        reasons.append(f"{prefix}:transition_not_lowered")
    return tuple(reasons)


def _ensure_model_state_slots(
    *,
    model: Any,
    model_index: int,
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
    bindings: Optional[BindingTableIR] = None,
) -> dict[int, int]:
    local_to_global: dict[int, int] = {}
    if bindings is not None:
        state_bindings = tuple(
            sorted(
                (
                    binding
                    for binding in bindings.bindings
                    if binding.kind == SYMBOL_STATE_SCALAR
                    and _model_state_is_numeric(model, str(binding.name))
                ),
                key=lambda binding: int(binding.slot),
            )
        )
        state_items = tuple(
            (int(binding.slot), str(binding.name), bool(binding.integer))
            for binding in state_bindings
        )
    else:
        integer_names = _model_integer_state_names(model)
        state_items = tuple(
            (local_id, state_name, state_name in integer_names)
            for local_id, state_name in enumerate(_model_state_names(model))
        )
    for local_id, state_name, is_integer in state_items:
        key = (model_index, state_name)
        if key not in state_ids:
            state_id = len(state_ids)
            state_ids[key] = state_id
            states.append(
                RustSimState(
                    name=f"{model_index}:{state_name}",
                    state_id=state_id,
                    initial_value=_model_state_value(model, state_name),
                    is_integer=is_integer,
                )
            )
        local_to_global[local_id] = state_ids[key]
    return local_to_global


def _ensure_model_param_slots(
    *,
    model: Any,
    model_index: int,
    param_ids: dict[tuple[int, str], int],
    params: list[RustSimParam],
) -> dict[int, int]:
    local_to_global: dict[int, int] = {}
    for local_id, param_name, value in _model_params(model):
        key = (model_index, param_name)
        if key not in param_ids:
            param_id = len(param_ids)
            param_ids[key] = param_id
            params.append(
                RustSimParam(
                    name=f"{model_index}:{param_name}",
                    param_id=param_id,
                    value=float(value),
                )
            )
        local_to_global[local_id] = param_ids[key]
    return local_to_global


def _node_slot_maps(
    *,
    model: Any,
    bindings: BindingTableIR,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
) -> tuple[dict[str, int], dict[int, int]]:
    local_node_slots: dict[str, int] = {}
    local_to_global: dict[int, int] = {}
    for binding in bindings.bindings:
        if binding.kind != SYMBOL_PORT:
            continue
        external = _external_node(model, binding.name)
        global_id = _add_node(external, node_ids, nodes)
        local_slot = int(binding.slot)
        local_node_slots[binding.name] = local_slot
        local_to_global[local_slot] = global_id
    return local_node_slots, local_to_global


def _iter_static_branch_node_names(expr_ir: ExprIR):
    if isinstance(expr_ir, BranchAccessIR):
        node1_name = static_node_ref_name(
            expr_ir.node1,
            expr_ir.node1_index,
            expr_ir.node1_index2,
        )
        if expr_ir.node2 is not None:
            node2_name = static_node_ref_name(
                expr_ir.node2,
                expr_ir.node2_index,
                expr_ir.node2_index2,
            )
        else:
            node2_name = None
        if expr_ir.access_type == "I" and node1_name is not None and node2_name is not None:
            yield _branch_current_node_name(node1_name, node2_name)
        else:
            if node1_name is not None:
                yield node1_name
            if node2_name is not None:
                yield node2_name
        for child in (
            expr_ir.node1_index,
            expr_ir.node1_index2,
            expr_ir.node2_index,
            expr_ir.node2_index2,
        ):
            if child is not None:
                yield from _iter_static_branch_node_names(child)
        return
    if isinstance(expr_ir, ArrayAccessIR):
        yield from _iter_static_branch_node_names(expr_ir.index)
        return
    if isinstance(expr_ir, BinaryExprIR):
        yield from _iter_static_branch_node_names(expr_ir.left)
        yield from _iter_static_branch_node_names(expr_ir.right)
        return
    if isinstance(expr_ir, UnaryExprIR):
        yield from _iter_static_branch_node_names(expr_ir.operand)
        return
    if isinstance(expr_ir, TernaryExprIR):
        yield from _iter_static_branch_node_names(expr_ir.cond)
        yield from _iter_static_branch_node_names(expr_ir.true_expr)
        yield from _iter_static_branch_node_names(expr_ir.false_expr)
        return
    if isinstance(expr_ir, FunctionCallIR):
        for arg in expr_ir.args:
            yield from _iter_static_branch_node_names(arg)


def _iter_static_branch_node_names_from_stmt(stmt_ir: object):
    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            yield from _iter_static_branch_node_names_from_stmt(child)
        return
    if isinstance(stmt_ir, AssignmentIR):
        yield from _iter_static_branch_node_names(stmt_ir.value)
        if isinstance(stmt_ir.target, ArrayAccessIR):
            yield from _iter_static_branch_node_names(stmt_ir.target.index)
        return
    if isinstance(stmt_ir, ContributionIR):
        yield from _iter_static_branch_node_names(stmt_ir.branch)
        yield from _iter_static_branch_node_names(stmt_ir.expr)
        return
    if isinstance(stmt_ir, EventStatementIR):
        yield from _iter_static_branch_node_names_from_event(stmt_ir.event)
        yield from _iter_static_branch_node_names_from_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, IfStatementIR):
        yield from _iter_static_branch_node_names(stmt_ir.cond)
        yield from _iter_static_branch_node_names_from_stmt(stmt_ir.then_body)
        if stmt_ir.else_body is not None:
            yield from _iter_static_branch_node_names_from_stmt(stmt_ir.else_body)
        return
    if isinstance(stmt_ir, (ForStatementIR, WhileStatementIR)):
        if isinstance(stmt_ir, ForStatementIR):
            unrolled = unroll_static_for_statement(stmt_ir)
            if unrolled is not None:
                yield from _iter_static_branch_node_names_from_stmt(unrolled)
                return
            yield from _iter_static_branch_node_names_from_stmt(stmt_ir.init)
            yield from _iter_static_branch_node_names(stmt_ir.cond)
            yield from _iter_static_branch_node_names_from_stmt(stmt_ir.update)
        else:
            yield from _iter_static_branch_node_names(stmt_ir.cond)
        yield from _iter_static_branch_node_names_from_stmt(stmt_ir.body)
        return
    if isinstance(stmt_ir, CaseStatementIR):
        yield from _iter_static_branch_node_names(stmt_ir.expr)
        for item in stmt_ir.items:
            for value in item.values:
                yield from _iter_static_branch_node_names(value)
            yield from _iter_static_branch_node_names_from_stmt(item.body)


def _iter_static_branch_node_names_from_event(event_ir: EventIR):
    if isinstance(event_ir, CombinedEventIR):
        for child in event_ir.events:
            yield from _iter_static_branch_node_names_from_event(child)
        return
    if not isinstance(event_ir, EventTriggerIR):
        return
    for expr in event_ir.args:
        yield from _iter_static_branch_node_names(expr)
    for expr in (event_ir.time_tol, event_ir.expr_tol):
        if expr is not None:
            yield from _iter_static_branch_node_names(expr)


def _extend_node_slots_from_static_branches(
    *,
    model: Any,
    stmt_ir: object,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    local_node_slots: dict[str, int],
    node_slot_to_global: dict[int, int],
) -> None:
    next_slot = max(local_node_slots.values(), default=-1) + 1
    for local_name in sorted(set(_iter_static_branch_node_names_from_stmt(stmt_ir))):
        if local_name in local_node_slots:
            continue
        local_node_slots[local_name] = next_slot
        node_slot_to_global[next_slot] = _add_node(
            _external_node(model, local_name),
            node_ids,
            nodes,
        )
        next_slot += 1


def _remap_body_expr_ops(
    expr_ops: Tuple[BodyExprOp, ...],
    *,
    node_slot_to_global: Mapping[int, int],
    state_slot_to_global: Mapping[int, int],
    param_slot_to_global: Mapping[int, int],
) -> Tuple[BodyExprOp, ...]:
    remapped: list[BodyExprOp] = []
    for op in expr_ops:
        index = int(op.index)
        if op.op_kind == BODY_EXPR_READ_NODE:
            index = int(node_slot_to_global.get(index, index))
        elif op.op_kind == BODY_EXPR_READ_STATE:
            index = int(state_slot_to_global.get(index, index))
        elif op.op_kind == BODY_EXPR_READ_PARAM:
            index = int(param_slot_to_global.get(index, index))
        remapped.append(
            BodyExprOp(
                op_kind=int(op.op_kind),
                index=index,
                value=float(op.value),
            )
        )
    return tuple(remapped)


def _append_expr_segment(
    expr_ops: list[BodyExprOp],
    segment: Tuple[BodyExprOp, ...],
    *,
    node_slot_to_global: Mapping[int, int],
    state_slot_to_global: Mapping[int, int],
    param_slot_to_global: Mapping[int, int],
) -> tuple[int, int]:
    start = len(expr_ops)
    remapped = _remap_body_expr_ops(
        segment,
        node_slot_to_global=node_slot_to_global,
        state_slot_to_global=state_slot_to_global,
        param_slot_to_global=param_slot_to_global,
    )
    expr_ops.extend(remapped)
    return start, len(remapped)


def _append_body_program(
    body_stmt_ops: list[BodyStmtOp],
    body_expr_ops: list[BodyExprOp],
    program,
    *,
    node_slot_to_global: Mapping[int, int],
    state_slot_to_global: Mapping[int, int],
    param_slot_to_global: Mapping[int, int],
    side_effect_slot_offset: int = 0,
) -> tuple[int, int]:
    stmt_start = len(body_stmt_ops)
    for stmt in tuple(getattr(program, "stmt_ops", ()) or ()):
        target_id = int(stmt.target_id)
        if int(stmt.target_kind) == BODY_STMT_FILE_OPEN:
            expr_start = int(stmt.expr_start) + int(side_effect_slot_offset)
            expr_count = 0
            target_id = int(state_slot_to_global.get(target_id, target_id))
        else:
            expr_start, expr_count = _append_expr_segment(
                body_expr_ops,
                tuple(getattr(program, "expr_ops", ()) or ())[
                    int(stmt.expr_start) : int(stmt.expr_start) + int(stmt.expr_count)
                ],
                node_slot_to_global=node_slot_to_global,
                state_slot_to_global=state_slot_to_global,
                param_slot_to_global=param_slot_to_global,
            )
            if int(stmt.target_kind) in {BODY_STMT_FILE_SCANF, BODY_STMT_FILE_GETS}:
                spec_pos = expr_start + expr_count - 1
                if expr_count <= 0 or spec_pos >= len(body_expr_ops):
                    continue
                spec_op = body_expr_ops[spec_pos]
                body_expr_ops[spec_pos] = BodyExprOp(
                    op_kind=int(spec_op.op_kind),
                    index=int(spec_op.index),
                    value=float(spec_op.value) + float(side_effect_slot_offset),
                )
        if stmt.target_kind == BODY_TARGET_NODE:
            target_id = int(node_slot_to_global.get(target_id, target_id))
        elif stmt.target_kind == BODY_TARGET_STATE or int(stmt.target_kind) in {
            BODY_STMT_FILE_SCANF,
            BODY_STMT_FILE_TELL,
            BODY_STMT_FILE_SEEK,
        }:
            target_id = int(state_slot_to_global.get(target_id, target_id))
        elif int(stmt.target_kind) in {
            BODY_STMT_FILE_WRITE,
            BODY_STMT_FILE_CLOSE,
            BODY_STMT_STRING_WRITE,
            BODY_STMT_STROBE,
        }:
            target_id += int(side_effect_slot_offset)
        body_stmt_ops.append(
            BodyStmtOp(
                target_kind=int(stmt.target_kind),
                target_id=target_id,
                expr_start=expr_start,
                expr_count=expr_count,
                target_integer=bool(stmt.target_integer),
            )
        )
    return stmt_start, len(body_stmt_ops) - stmt_start


def _remap_side_effect_targets(
    effects: Iterable[RustSimSideEffect],
    state_slot_to_global: Mapping[int, int],
) -> tuple[RustSimSideEffect, ...]:
    remapped: list[RustSimSideEffect] = []
    for effect in effects:
        target_ids = tuple(
            int(state_slot_to_global.get(int(slot), int(slot)))
            for slot in tuple(getattr(effect, "target_ids", ()) or ())
        )
        remapped.append(
            RustSimSideEffect(
                kind=str(effect.kind),
                filename=str(effect.filename),
                mode=str(effect.mode),
                fmt=str(effect.fmt),
                target_ids=target_ids,
                target_integers=tuple(bool(v) for v in effect.target_integers),
                append_newline=bool(effect.append_newline),
                target=str(effect.target),
                format_args=tuple(effect.format_args),
                owner=effect.owner,
            )
        )
    return tuple(remapped)


def _is_continuous_body_stmt(stmt_ir: object) -> bool:
    """Return True for ordered non-event state writes that Rust can own per step."""

    if isinstance(stmt_ir, AssignmentIR):
        return True
    if isinstance(stmt_ir, ContributionIR):
        return not _expr_contains_transition_or_slew_call(stmt_ir.expr)
    if isinstance(stmt_ir, IfStatementIR):
        return _is_continuous_body_stmt(stmt_ir.then_body) and (
            stmt_ir.else_body is None or _is_continuous_body_stmt(stmt_ir.else_body)
        )
    if isinstance(stmt_ir, ForStatementIR):
        unrolled = unroll_static_for_statement(stmt_ir)
        return (
            unrolled is not None
            and not _stmt_has_rustsim_event_transition_candidate(unrolled)
            and _is_continuous_body_stmt(unrolled)
        )
    if isinstance(stmt_ir, CaseStatementIR):
        return all(_is_continuous_body_stmt(item.body) for item in stmt_ir.items)
    if isinstance(stmt_ir, SystemTaskIR):
        return stmt_ir.name in {
            "$bound_step",
            "$cds_set_rf_source_info",
            "$cds_violation",
            "$discontinuity",
            "$display",
            "$strobe",
            "$debug",
            "$warning",
            "$info",
            "$error",
        }
    if isinstance(stmt_ir, BlockIR):
        return all(_is_continuous_body_stmt(child) for child in stmt_ir.statements)
    return False


def _is_branch_ddt_contribution_stmt(stmt_ir: object) -> bool:
    if not isinstance(stmt_ir, ContributionIR):
        return False
    if stmt_ir.branch.access_type != "I":
        return False
    expr = stmt_ir.expr
    if isinstance(expr, FunctionCallIR) and str(expr.name) == "ddt":
        return True
    if isinstance(expr, UnaryExprIR):
        return _is_branch_ddt_contribution_stmt(
            ContributionIR(stmt_ir.branch, expr.operand)
        )
    if isinstance(expr, BinaryExprIR) and expr.op == "*":
        return (
            isinstance(expr.left, FunctionCallIR)
            and str(expr.left.name) == "ddt"
        ) or (
            isinstance(expr.right, FunctionCallIR)
            and str(expr.right.name) == "ddt"
        )
    return False


def _collect_contributed_nodes(stmt_ir: object) -> frozenset[str]:
    nodes: set[str] = set()

    if isinstance(stmt_ir, BlockIR):
        for child in stmt_ir.statements:
            nodes.update(_collect_contributed_nodes(child))
        return frozenset(nodes)

    if isinstance(stmt_ir, ContributionIR):
        if stmt_ir.branch.access_type == "V":
            # Only node1 is DRIVEN by a voltage contribution; node2 is the
            # reference (V(n1, n2) <+ x sets n1 = n2 + x). Counting node2 as
            # contributed wrongly classified every cross expression that
            # references a rail (e.g. V(in, VSS) with VSS used as the node2 of
            # output contributions) as post-phase, blocking pre-phase-only
            # features such as the cross-acceptance law mode for the common
            # ground-referenced benchmark style.
            node1_name = static_node_ref_name(
                stmt_ir.branch.node1,
                stmt_ir.branch.node1_index,
                stmt_ir.branch.node1_index2,
            )
            nodes.add(str(node1_name or stmt_ir.branch.node1))
        elif stmt_ir.branch.access_type == "I":
            node1_name = static_node_ref_name(
                stmt_ir.branch.node1,
                stmt_ir.branch.node1_index,
                stmt_ir.branch.node1_index2,
            )
            node2_name = (
                None
                if stmt_ir.branch.node2 is None
                else static_node_ref_name(
                    stmt_ir.branch.node2,
                    stmt_ir.branch.node2_index,
                    stmt_ir.branch.node2_index2,
                )
            )
            if node1_name is not None and node2_name is not None:
                nodes.add(_branch_current_node_name(node1_name, node2_name))
        return frozenset(nodes)

    if isinstance(stmt_ir, EventStatementIR):
        return _collect_contributed_nodes(stmt_ir.body)

    if isinstance(stmt_ir, IfStatementIR):
        nodes.update(_collect_contributed_nodes(stmt_ir.then_body))
        if stmt_ir.else_body is not None:
            nodes.update(_collect_contributed_nodes(stmt_ir.else_body))
        return frozenset(nodes)

    if isinstance(stmt_ir, (ForStatementIR, WhileStatementIR)):
        return _collect_contributed_nodes(stmt_ir.body)

    if isinstance(stmt_ir, CaseStatementIR):
        for item in stmt_ir.items:
            nodes.update(_collect_contributed_nodes(item.body))
        return frozenset(nodes)

    return frozenset()


def _collect_global_contributed_nodes(models: Iterable[Any]) -> frozenset[str]:
    nodes: set[str] = set()
    for model in models:
        model_cls = getattr(model, "__class__", type(model))
        module = getattr(model_cls, "_module_ast", None)
        analog_block = (
            getattr(module, "analog_block", None) if module is not None else None
        )
        body_ast = getattr(analog_block, "body", None)
        if body_ast is None:
            continue
        try:
            body_ir = _lower_module_body_with_user_function_inlining(module)
        except Exception:
            continue
        if body_ir is None:
            continue
        for local_name in _collect_contributed_nodes(body_ir):
            nodes.add(_external_node(model, local_name))
    return frozenset(nodes)


def _flatten_models_child_first(models: Iterable[Any]) -> Tuple[Any, ...]:
    """Return a deterministic child-before-parent lowering order.

    The Python backend evaluates hierarchical instances before the parent body
    observes their internal nets.  Rust full-model lowering uses the same
    ordering so simple hierarchy can be represented as one flat opcode program.
    """

    flattened: list[Any] = []
    visiting: set[int] = set()

    def visit(model: Any) -> None:
        ident = id(model)
        if ident in visiting:
            return
        visiting.add(ident)
        for child in tuple(getattr(model, "_child_models", ()) or ()):
            visit(child)
        flattened.append(model)

    for top in models:
        visit(top)
    return tuple(flattened)


def _expr_references_nodes(expr_ir: ExprIR, nodes: frozenset[str]) -> bool:
    if not nodes:
        return False
    if isinstance(expr_ir, BranchAccessIR):
        return (
            expr_ir.node1 in nodes
            or (expr_ir.node2 is not None and expr_ir.node2 in nodes)
            or (
                expr_ir.node1_index is not None
                and _expr_references_nodes(expr_ir.node1_index, nodes)
            )
            or (
                expr_ir.node1_index2 is not None
                and _expr_references_nodes(expr_ir.node1_index2, nodes)
            )
            or (
                expr_ir.node2_index is not None
                and _expr_references_nodes(expr_ir.node2_index, nodes)
            )
            or (
                expr_ir.node2_index2 is not None
                and _expr_references_nodes(expr_ir.node2_index2, nodes)
            )
        )
    if isinstance(expr_ir, ArrayAccessIR):
        return _expr_references_nodes(expr_ir.index, nodes)
    if isinstance(expr_ir, BinaryExprIR):
        return _expr_references_nodes(expr_ir.left, nodes) or _expr_references_nodes(
            expr_ir.right,
            nodes,
        )
    if isinstance(expr_ir, UnaryExprIR):
        return _expr_references_nodes(expr_ir.operand, nodes)
    if isinstance(expr_ir, TernaryExprIR):
        return (
            _expr_references_nodes(expr_ir.cond, nodes)
            or _expr_references_nodes(expr_ir.true_expr, nodes)
            or _expr_references_nodes(expr_ir.false_expr, nodes)
        )
    if isinstance(expr_ir, FunctionCallIR):
        return any(_expr_references_nodes(arg, nodes) for arg in expr_ir.args)
    return False


def _event_phase(event_ir: EventIR, contributed_nodes: frozenset[str]) -> int:
    if isinstance(event_ir, CombinedEventIR):
        if any(_event_phase(child, contributed_nodes) == EVENT_PHASE_POST for child in event_ir.events):
            return EVENT_PHASE_POST
        return EVENT_PHASE_PRE
    if not isinstance(event_ir, EventTriggerIR):
        return EVENT_PHASE_PRE
    event_type = str(event_ir.event_type).lower()
    if event_type in {EVENT_DUE_CROSS, EVENT_DUE_ABOVE} and event_ir.args:
        if _expr_references_nodes(event_ir.args[0], contributed_nodes):
            return EVENT_PHASE_POST
    return EVENT_PHASE_PRE


def _event_trigger_phases(
    event_ir: EventIR,
    contributed_nodes: frozenset[str],
) -> Tuple[int, ...]:
    if isinstance(event_ir, CombinedEventIR):
        phases: list[int] = []
        for child in event_ir.events:
            phases.extend(_event_trigger_phases(child, contributed_nodes))
        return tuple(phases)
    return (_event_phase(event_ir, contributed_nodes),)


def _prefer_existing_timer_static_linear_path(model_cls: Any) -> bool:
    """Keep stable periodic timer-static-linear models on their older Rust path."""

    if not tuple(getattr(model_cls, "_event_timer_static_linear_ir_ops", ()) or ()):
        return False
    if tuple(getattr(model_cls, "_transition_target_ir_ops", ()) or ()):
        return False
    return True


def _stmt_has_display_strobe(stmt_ir: object) -> bool:
    if isinstance(stmt_ir, BlockIR):
        return any(_stmt_has_display_strobe(child) for child in stmt_ir.statements)
    if isinstance(stmt_ir, EventStatementIR):
        return _stmt_has_display_strobe(stmt_ir.body)
    if isinstance(stmt_ir, IfStatementIR):
        return _stmt_has_display_strobe(stmt_ir.then_body) or (
            stmt_ir.else_body is not None and _stmt_has_display_strobe(stmt_ir.else_body)
        )
    if isinstance(stmt_ir, ForStatementIR):
        unrolled = unroll_static_for_statement(stmt_ir)
        if unrolled is not None:
            return _stmt_has_display_strobe(unrolled)
        return (
            _stmt_has_display_strobe(stmt_ir.init)
            or _stmt_has_display_strobe(stmt_ir.update)
            or _stmt_has_display_strobe(stmt_ir.body)
        )
    if isinstance(stmt_ir, WhileStatementIR):
        return _stmt_has_display_strobe(stmt_ir.body)
    if isinstance(stmt_ir, CaseStatementIR):
        return any(_stmt_has_display_strobe(item.body) for item in stmt_ir.items)
    if isinstance(stmt_ir, SystemTaskIR):
        return stmt_ir.name in {
            "$display",
            "$strobe",
            "$debug",
            "$warning",
            "$info",
            "$error",
        }
    return False


def _event_ir_is_pure_initial_step(event_ir: object) -> bool:
    return (
        isinstance(event_ir, EventTriggerIR)
        and str(event_ir.event_type).upper() == "INITIAL_STEP"
    )


def _expr_has_initial_file_read(expr_ir: object) -> bool:
    if isinstance(expr_ir, FunctionCallIR):
        if str(expr_ir.name) in _INITIAL_FILE_READ_FUNCTIONS:
            return True
        return any(_expr_has_initial_file_read(arg) for arg in expr_ir.args)
    if isinstance(expr_ir, BinaryExprIR):
        return _expr_has_initial_file_read(expr_ir.left) or _expr_has_initial_file_read(
            expr_ir.right
        )
    if isinstance(expr_ir, UnaryExprIR):
        return _expr_has_initial_file_read(expr_ir.operand)
    if isinstance(expr_ir, TernaryExprIR):
        return (
            _expr_has_initial_file_read(expr_ir.cond)
            or _expr_has_initial_file_read(expr_ir.true_expr)
            or _expr_has_initial_file_read(expr_ir.false_expr)
        )
    if isinstance(expr_ir, ArrayAccessIR):
        return _expr_has_initial_file_read(expr_ir.index)
    if isinstance(expr_ir, BranchAccessIR):
        return any(
            child is not None and _expr_has_initial_file_read(child)
            for child in (
                expr_ir.node1_index,
                expr_ir.node1_index2,
                expr_ir.node2_index,
                expr_ir.node2_index2,
            )
        )
    return False


def _stmt_has_initial_file_read(stmt_ir: object) -> bool:
    if isinstance(stmt_ir, BlockIR):
        return any(_stmt_has_initial_file_read(child) for child in stmt_ir.statements)
    if isinstance(stmt_ir, EventStatementIR):
        return _stmt_has_initial_file_read(stmt_ir.body)
    if isinstance(stmt_ir, AssignmentIR):
        return _expr_has_initial_file_read(stmt_ir.value)
    if isinstance(stmt_ir, SystemTaskIR):
        return str(stmt_ir.name) in _INITIAL_FILE_READ_TASKS or any(
            _expr_has_initial_file_read(arg) for arg in stmt_ir.args
        )
    if isinstance(stmt_ir, IfStatementIR):
        return (
            _expr_has_initial_file_read(stmt_ir.cond)
            or _stmt_has_initial_file_read(stmt_ir.then_body)
            or (
                stmt_ir.else_body is not None
                and _stmt_has_initial_file_read(stmt_ir.else_body)
            )
        )
    if isinstance(stmt_ir, ForStatementIR):
        return (
            _stmt_has_initial_file_read(stmt_ir.init)
            or _expr_has_initial_file_read(stmt_ir.cond)
            or _stmt_has_initial_file_read(stmt_ir.update)
            or _stmt_has_initial_file_read(stmt_ir.body)
        )
    if isinstance(stmt_ir, WhileStatementIR):
        return _expr_has_initial_file_read(stmt_ir.cond) or _stmt_has_initial_file_read(
            stmt_ir.body
        )
    if isinstance(stmt_ir, CaseStatementIR):
        return _expr_has_initial_file_read(stmt_ir.expr) or any(
            any(_expr_has_initial_file_read(value) for value in item.values)
            or _stmt_has_initial_file_read(item.body)
            for item in stmt_ir.items
        )
    if isinstance(stmt_ir, ContributionIR):
        return _expr_has_initial_file_read(stmt_ir.expr)
    return False


def _strip_preapplied_initial_file_read_events(stmt_ir: object) -> object:
    if isinstance(stmt_ir, BlockIR):
        return BlockIR(
            tuple(
                _strip_preapplied_initial_file_read_events(child)
                for child in stmt_ir.statements
            )
        )
    if (
        isinstance(stmt_ir, EventStatementIR)
        and _event_ir_is_pure_initial_step(stmt_ir.event)
        and _stmt_has_initial_file_read(stmt_ir.body)
    ):
        return EventStatementIR(stmt_ir.event, BlockIR(()))
    return stmt_ir


def model_has_pure_initial_step_file_read(model: Any) -> bool:
    """Return True when Python must preapply an initial_step sidecar read.

    RustSimProgram owns numeric state and waveform evolution, but it does not
    currently carry file handles or string buffers in the C ABI.  For the
    Spectre-supported subset where a pure initial_step event ingests a sidecar
    file and later Rust-owned events use the resulting numeric state, Python can
    execute that initial event before lowering and Rust can safely start from
    the updated state vector.
    """

    model_cls = getattr(model, "__class__", type(model))
    module = getattr(model_cls, "_module_ast", None)
    analog_block = getattr(module, "analog_block", None) if module is not None else None
    body_ast = getattr(analog_block, "body", None)
    if body_ast is None:
        return False
    try:
        body_ir = lower_stmt(body_ast)
    except Exception:
        return False
    if not isinstance(body_ir, BlockIR):
        return False
    return any(
        isinstance(stmt, EventStatementIR)
        and _event_ir_is_pure_initial_step(stmt.event)
        and _stmt_has_initial_file_read(stmt.body)
        for stmt in body_ir.statements
    )


def _stmt_has_rustsim_event_transition_candidate(stmt_ir: object) -> bool:
    if isinstance(stmt_ir, BlockIR):
        return any(
            _stmt_has_rustsim_event_transition_candidate(child)
            for child in stmt_ir.statements
        )
    if isinstance(stmt_ir, EventStatementIR):
        return True
    if isinstance(stmt_ir, ContributionIR):
        return _expr_contains_transition_or_slew_call(stmt_ir.expr)
    if isinstance(stmt_ir, IfStatementIR):
        return _stmt_has_rustsim_event_transition_candidate(stmt_ir.then_body) or (
            stmt_ir.else_body is not None
            and _stmt_has_rustsim_event_transition_candidate(stmt_ir.else_body)
        )
    if isinstance(stmt_ir, (ForStatementIR, WhileStatementIR)):
        return _stmt_has_rustsim_event_transition_candidate(stmt_ir.body)
    if isinstance(stmt_ir, CaseStatementIR):
        return any(
            _stmt_has_rustsim_event_transition_candidate(item.body)
            for item in stmt_ir.items
        )
    return False


def _expr_contains_transition_call(expr_ir: ExprIR) -> bool:
    if isinstance(expr_ir, FunctionCallIR):
        return str(expr_ir.name) == "transition" or any(
            _expr_contains_transition_call(arg) for arg in expr_ir.args
        )
    if isinstance(expr_ir, BinaryExprIR):
        return _expr_contains_transition_call(
            expr_ir.left
        ) or _expr_contains_transition_call(expr_ir.right)
    if isinstance(expr_ir, UnaryExprIR):
        return _expr_contains_transition_call(expr_ir.operand)
    if isinstance(expr_ir, TernaryExprIR):
        return (
            _expr_contains_transition_call(expr_ir.cond)
            or _expr_contains_transition_call(expr_ir.true_expr)
            or _expr_contains_transition_call(expr_ir.false_expr)
        )
    if isinstance(expr_ir, ArrayAccessIR):
        return _expr_contains_transition_call(expr_ir.index)
    if isinstance(expr_ir, BranchAccessIR):
        return any(
            child is not None and _expr_contains_transition_call(child)
            for child in (
                expr_ir.node1_index,
                expr_ir.node1_index2,
                expr_ir.node2_index,
                expr_ir.node2_index2,
            )
        )
    return False


def _expr_contains_transition_or_slew_call(expr_ir: ExprIR) -> bool:
    if isinstance(expr_ir, FunctionCallIR):
        name = str(expr_ir.name)
        return name in {"transition", "slew"} or any(
            _expr_contains_transition_or_slew_call(arg) for arg in expr_ir.args
        )
    if isinstance(expr_ir, BinaryExprIR):
        return _expr_contains_transition_or_slew_call(
            expr_ir.left
        ) or _expr_contains_transition_or_slew_call(expr_ir.right)
    if isinstance(expr_ir, UnaryExprIR):
        return _expr_contains_transition_or_slew_call(expr_ir.operand)
    if isinstance(expr_ir, TernaryExprIR):
        return (
            _expr_contains_transition_or_slew_call(expr_ir.cond)
            or _expr_contains_transition_or_slew_call(expr_ir.true_expr)
            or _expr_contains_transition_or_slew_call(expr_ir.false_expr)
        )
    if isinstance(expr_ir, ArrayAccessIR):
        return _expr_contains_transition_or_slew_call(expr_ir.index)
    if isinstance(expr_ir, BranchAccessIR):
        return any(
            child is not None and _expr_contains_transition_or_slew_call(child)
            for child in (
                expr_ir.node1_index,
                expr_ir.node1_index2,
                expr_ir.node2_index,
                expr_ir.node2_index2,
            )
        )
    return False


def _model_has_rustsim_event_transition_candidate(model_cls: Any) -> bool:
    module = getattr(model_cls, "_module_ast", None)
    body_ir = _lower_module_body_with_user_function_inlining(module)
    if body_ir is None:
        return False
    return _stmt_has_rustsim_event_transition_candidate(body_ir)


def _model_has_rustsim_continuous_body_candidate(model_cls: Any) -> bool:
    module = getattr(model_cls, "_module_ast", None)
    body_ir = _lower_module_body_with_user_function_inlining(module)
    if body_ir is None:
        return False
    return any(_is_continuous_body_stmt(stmt) for stmt in body_ir.statements)


def _convert_continuous_linear_ops(
    *,
    model: Any,
    model_index: int,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
) -> tuple[Tuple[RustSimLinearOp, ...], Tuple[str, ...]]:
    model_cls = getattr(model, "__class__", type(model))
    raw_ops = tuple(
        getattr(model_cls, "_evaluate_ir_static_linear_non_event_ops", ()) or ()
    )
    if not raw_ops:
        raw_ops = tuple(getattr(model_cls, "_evaluate_ir_static_linear_ops", ()) or ())
    if not raw_ops:
        return (), (f"model:{model_index}:{model_cls.__name__}:no_continuous_linear_ir",)

    integer_names = _model_integer_state_names(model)
    for state_name in _model_state_names(model):
        key = (model_index, state_name)
        if key not in state_ids:
            state_id = len(state_ids)
            state_ids[key] = state_id
            states.append(
                RustSimState(
                    name=f"{model_index}:{state_name}",
                    state_id=state_id,
                    initial_value=_model_state_value(model, state_name),
                    is_integer=state_name in integer_names,
                )
            )

    try:
        ops = normalize_linear_ops(raw_ops)
    except Exception as exc:
        return (), (f"model:{model_index}:{model_cls.__name__}:linear_ir_decode_failed:{exc}",)

    reasons: list[str] = []

    def convert_terms(ir_terms) -> Tuple[RustSimLinearTerm, ...]:
        terms: list[RustSimLinearTerm] = []
        for term in ir_terms:
            gain, reason = _scalar(model, term.gain)
            if reason is not None or gain is None:
                reasons.append(
                    f"model:{model_index}:{model_cls.__name__}:term_gain:{reason}"
                )
                continue
            if term.source_kind == SOURCE_NODE:
                node_name = _external_node(model, term.source_name)
                source_id = _add_node(node_name, node_ids, nodes)
            elif term.source_kind == SOURCE_STATE:
                key = (model_index, str(term.source_name))
                if key not in state_ids:
                    state_id = len(state_ids)
                    state_ids[key] = state_id
                    states.append(
                        RustSimState(
                            name=f"{model_index}:{term.source_name}",
                            state_id=state_id,
                            initial_value=_model_state_value(
                                model,
                                str(term.source_name),
                            ),
                            is_integer=str(term.source_name) in integer_names,
                        )
                    )
                source_id = state_ids[key]
            else:
                reasons.append(
                    f"model:{model_index}:{model_cls.__name__}:unsupported_source_kind:{term.source_kind}"
                )
                continue
            terms.append(
                RustSimLinearTerm(
                    source_kind=int(term.source_kind),
                    source_id=source_id,
                    gain=gain,
                )
            )
        return tuple(terms)

    def convert_condition(condition) -> Optional[RustSimLinearCondition]:
        if condition is None:
            return None
        left_bias, reason = _scalar(model, condition.left_bias)
        if reason is not None or left_bias is None:
            reasons.append(
                f"model:{model_index}:{model_cls.__name__}:condition_left:{reason}"
            )
            return None
        right_bias, reason = _scalar(model, condition.right_bias)
        if reason is not None or right_bias is None:
            reasons.append(
                f"model:{model_index}:{model_cls.__name__}:condition_right:{reason}"
            )
            return None
        return RustSimLinearCondition(
            op_kind=int(condition.op_kind),
            left_bias=left_bias,
            left_terms=convert_terms(condition.left_terms),
            right_bias=right_bias,
            right_terms=convert_terms(condition.right_terms),
        )

    converted: list[RustSimLinearOp] = []
    for op in ops:
        bias, reason = _scalar(model, op.bias)
        if reason is not None or bias is None:
            reasons.append(f"model:{model_index}:{model_cls.__name__}:bias:{reason}")
            continue
        false_bias, reason = _scalar(model, op.false_bias)
        if reason is not None or false_bias is None:
            reasons.append(
                f"model:{model_index}:{model_cls.__name__}:false_bias:{reason}"
            )
            continue
        if op.target_kind == TARGET_NODE:
            target_id = _add_node(_external_node(model, op.target_name), node_ids, nodes)
        elif op.target_kind == TARGET_STATE:
            key = (model_index, str(op.target_name))
            if key not in state_ids:
                state_id = len(state_ids)
                state_ids[key] = state_id
                states.append(
                    RustSimState(
                        name=f"{model_index}:{op.target_name}",
                        state_id=state_id,
                        initial_value=_model_state_value(
                            model,
                            str(op.target_name),
                        ),
                        is_integer=bool(op.target_integer),
                    )
                )
            target_id = state_ids[key]
        else:
            reasons.append(
                f"model:{model_index}:{model_cls.__name__}:unsupported_target_kind:{op.target_kind}"
            )
            continue
        converted.append(
            RustSimLinearOp(
                target_kind=int(op.target_kind),
                target_id=target_id,
                bias=bias,
                terms=convert_terms(op.terms),
                condition=convert_condition(op.condition),
                false_bias=false_bias,
                false_terms=convert_terms(op.false_terms),
                target_integer=bool(op.target_integer),
            )
        )

    return tuple(converted), tuple(reasons)


def _convert_sampled_zi_nd_ops(
    *,
    model: Any,
    model_index: int,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    source_data: list[float],
) -> tuple[Tuple[RustSimZiNdOp, ...], Tuple[str, ...]]:
    model_cls = getattr(model, "__class__", type(model))
    raw_ops = tuple(getattr(model_cls, "_evaluate_ir_sampled_zi_nd_ops", ()) or ())
    if not raw_ops:
        return (), ()

    converted: list[RustSimZiNdOp] = []
    reasons: list[str] = []
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    for op_index, raw_op in enumerate(raw_ops):
        if len(raw_op) != 5:
            reasons.append(f"{prefix}:zi_nd:{op_index}:malformed_ir")
            continue
        target_name, input_name, raw_num, raw_den, raw_interval = raw_op
        target_id = _add_node(_external_node(model, str(target_name)), node_ids, nodes)
        input_id = _add_node(_external_node(model, str(input_name)), node_ids, nodes)

        num_values: list[float] = []
        for coeff_index, value in enumerate(tuple(raw_num)):
            coeff, reason = _scalar(model, value)
            if reason is not None or coeff is None:
                reasons.append(
                    f"{prefix}:zi_nd:{op_index}:num:{coeff_index}:{reason}"
                )
                continue
            num_values.append(float(coeff))
        den_values: list[float] = []
        for coeff_index, value in enumerate(tuple(raw_den)):
            coeff, reason = _scalar(model, value)
            if reason is not None or coeff is None:
                reasons.append(
                    f"{prefix}:zi_nd:{op_index}:den:{coeff_index}:{reason}"
                )
                continue
            den_values.append(float(coeff))
        interval, reason = _scalar(model, raw_interval)
        if reason is not None or interval is None:
            reasons.append(f"{prefix}:zi_nd:{op_index}:interval:{reason}")
            continue
        if not num_values:
            reasons.append(f"{prefix}:zi_nd:{op_index}:empty_num")
            continue
        if not den_values:
            reasons.append(f"{prefix}:zi_nd:{op_index}:empty_den")
            continue
        if float(den_values[0]) == 0.0:
            reasons.append(f"{prefix}:zi_nd:{op_index}:zero_den0")
            continue
        if not math.isfinite(float(interval)):
            reasons.append(f"{prefix}:zi_nd:{op_index}:nonfinite_interval")
            continue

        num_start = len(source_data)
        source_data.extend(num_values)
        den_start = len(source_data)
        source_data.extend(den_values)
        converted.append(
            RustSimZiNdOp(
                target_node_id=target_id,
                input_node_id=input_id,
                num_start=num_start,
                num_count=len(num_values),
                den_start=den_start,
                den_count=len(den_values),
                interval=float(interval),
            )
        )

    return tuple(converted), tuple(reasons)


def _ast_number(value: float) -> AstNumberLiteral:
    return AstNumberLiteral(float(value), raw=str(float(value)))


def _ast_number_value(expr: Any) -> Optional[float]:
    if isinstance(expr, AstNumberLiteral):
        try:
            return float(expr.value)
        except (TypeError, ValueError):
            return None
    return None


def _is_ast_one(expr: Any) -> bool:
    value = _ast_number_value(expr)
    return value is not None and abs(value - 1.0) <= 1.0e-18


def _scale_ast_scalar(left: Any, right: Any) -> Any:
    if _is_ast_one(left):
        return right
    if _is_ast_one(right):
        return left
    left_value = _ast_number_value(left)
    right_value = _ast_number_value(right)
    if left_value is not None and right_value is not None:
        return _ast_number(left_value * right_value)
    return AstBinaryExpr("*", left, right)


def _ast_static_scalar_expr(expr: Any) -> Any:
    if isinstance(expr, AstNumberLiteral):
        return float(expr.value)
    if isinstance(expr, AstIdentifier):
        return ("param", str(expr.name))
    if isinstance(expr, AstUnaryExpr):
        operand = _ast_static_scalar_expr(expr.operand)
        if expr.op == "+":
            return operand
        if expr.op == "-":
            if isinstance(operand, (int, float)):
                return -float(operand)
            return ("neg", operand)
        return expr
    if isinstance(expr, AstBinaryExpr):
        left = _ast_static_scalar_expr(expr.left)
        right = _ast_static_scalar_expr(expr.right)
        op = {
            "+": "add",
            "-": "sub",
            "*": "mul",
            "/": "div",
        }.get(str(expr.op))
        if op is None:
            return expr
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if op == "add":
                return float(left) + float(right)
            if op == "sub":
                return float(left) - float(right)
            if op == "mul":
                return float(left) * float(right)
            if op == "div" and float(right) != 0.0:
                return float(left) / float(right)
        return (op, left, right)
    return expr


def _is_plain_branch_access(expr: Any, access_type: str) -> bool:
    return (
        isinstance(expr, AstBranchAccess)
        and str(expr.access_type) == access_type
        and expr.node1_index is None
        and expr.node1_index2 is None
        and expr.node2_index is None
        and expr.node2_index2 is None
    )


def _match_scaled_idt_call(expr: Any) -> Optional[tuple[Any, AstFunctionCall]]:
    if isinstance(expr, AstFunctionCall) and str(expr.name).lower() == "idt":
        return _ast_number(1.0), expr
    if isinstance(expr, AstUnaryExpr):
        matched = _match_scaled_idt_call(expr.operand)
        if matched is None:
            return None
        gain_expr, call = matched
        if expr.op == "+":
            return gain_expr, call
        if expr.op == "-":
            return _scale_ast_scalar(_ast_number(-1.0), gain_expr), call
        return None
    if isinstance(expr, AstBinaryExpr) and expr.op == "*":
        left = _match_scaled_idt_call(expr.left)
        if left is not None:
            gain_expr, call = left
            return _scale_ast_scalar(expr.right, gain_expr), call
        right = _match_scaled_idt_call(expr.right)
        if right is not None:
            gain_expr, call = right
            return _scale_ast_scalar(expr.left, gain_expr), call
    return None


def _match_scaled_ddt_call(expr: Any) -> Optional[tuple[Any, AstFunctionCall]]:
    if isinstance(expr, AstFunctionCall) and str(expr.name).lower() == "ddt":
        return _ast_number(1.0), expr
    if isinstance(expr, AstUnaryExpr):
        matched = _match_scaled_ddt_call(expr.operand)
        if matched is None:
            return None
        gain_expr, call = matched
        if expr.op == "+":
            return gain_expr, call
        if expr.op == "-":
            return _scale_ast_scalar(_ast_number(-1.0), gain_expr), call
        return None
    if isinstance(expr, AstBinaryExpr) and expr.op == "*":
        left = _match_scaled_ddt_call(expr.left)
        if left is not None:
            gain_expr, call = left
            return _scale_ast_scalar(expr.right, gain_expr), call
        right = _match_scaled_ddt_call(expr.right)
        if right is not None:
            gain_expr, call = right
            return _scale_ast_scalar(expr.left, gain_expr), call
    return None


def _flatten_ast_block_statements(stmt: Any) -> tuple[Any, ...]:
    if isinstance(stmt, AstBlock):
        flattened: list[Any] = []
        for child in stmt.statements:
            flattened.extend(_flatten_ast_block_statements(child))
        return tuple(flattened)
    return (stmt,)


def _simple_user_function_return_expr(decl: Any) -> Optional[Any]:
    statements = _flatten_ast_block_statements(getattr(decl, "body", None))
    if len(statements) != 1:
        return None
    stmt = statements[0]
    if not isinstance(stmt, AstAssignment):
        return None
    target = stmt.target
    if not isinstance(target, AstIdentifier) or str(target.name) != str(decl.name):
        return None
    return stmt.value


def _simple_user_function_map(module: Any) -> dict[str, Any]:
    return {
        str(getattr(decl, "name", "")): decl
        for decl in tuple(getattr(module, "functions", ()) or ())
        if _simple_user_function_return_expr(decl) is not None
    }


def _inline_user_function_expr(
    expr: Any,
    functions: Mapping[str, Any],
    env: Optional[Mapping[str, Any]] = None,
    *,
    depth: int = 0,
) -> Any:
    if depth > 32:
        return copy.deepcopy(expr)
    env = env or {}
    if isinstance(expr, AstIdentifier):
        if expr.name in env:
            return copy.deepcopy(env[expr.name])
        return copy.deepcopy(expr)
    if isinstance(expr, AstNumberLiteral):
        return copy.deepcopy(expr)
    if isinstance(expr, AstArrayAccess):
        return AstArrayAccess(
            expr.name,
            _inline_user_function_expr(expr.index, functions, env, depth=depth),
            (
                _inline_user_function_expr(expr.index2, functions, env, depth=depth)
                if expr.index2 is not None
                else None
            ),
        )
    if isinstance(expr, AstBranchAccess):
        cloned = copy.deepcopy(expr)
        for attr in ("node1_index", "node2_index", "node1_index2", "node2_index2"):
            child = getattr(expr, attr, None)
            if child is not None:
                setattr(
                    cloned,
                    attr,
                    _inline_user_function_expr(child, functions, env, depth=depth),
                )
        return cloned
    if isinstance(expr, AstUnaryExpr):
        return AstUnaryExpr(
            expr.op,
            _inline_user_function_expr(expr.operand, functions, env, depth=depth),
        )
    if isinstance(expr, AstBinaryExpr):
        return AstBinaryExpr(
            expr.op,
            _inline_user_function_expr(expr.left, functions, env, depth=depth),
            _inline_user_function_expr(expr.right, functions, env, depth=depth),
        )
    if isinstance(expr, AstTernaryExpr):
        return AstTernaryExpr(
            _inline_user_function_expr(expr.cond, functions, env, depth=depth),
            _inline_user_function_expr(expr.true_expr, functions, env, depth=depth),
            _inline_user_function_expr(expr.false_expr, functions, env, depth=depth),
        )
    if isinstance(expr, AstFunctionCall):
        inlined_args = [
            _inline_user_function_expr(arg, functions, env, depth=depth)
            for arg in expr.args
        ]
        decl = functions.get(str(expr.name))
        if decl is None:
            return AstFunctionCall(expr.name, inlined_args)
        return_expr = _simple_user_function_return_expr(decl)
        args = tuple(getattr(decl, "args", ()) or ())
        if return_expr is None or len(args) != len(inlined_args):
            return AstFunctionCall(expr.name, inlined_args)
        local_env = dict(env)
        for arg_decl, arg_expr in zip(args, inlined_args):
            local_env[str(arg_decl.name)] = arg_expr
        return _inline_user_function_expr(
            return_expr,
            functions,
            local_env,
            depth=depth + 1,
        )
    if isinstance(expr, AstMethodCall):
        return AstMethodCall(
            expr.obj,
            expr.method,
            [
                _inline_user_function_expr(arg, functions, env, depth=depth)
                for arg in expr.args
            ],
        )
    return copy.deepcopy(expr)


def _inline_user_function_event(event: Any, functions: Mapping[str, Any]) -> Any:
    if isinstance(event, AstEventExpr):
        cloned = copy.deepcopy(event)
        cloned.args = [
            _inline_user_function_expr(arg, functions) for arg in event.args
        ]
        if cloned.time_tol_expr is not None:
            cloned.time_tol_expr = _inline_user_function_expr(
                cloned.time_tol_expr, functions
            )
        if cloned.expr_tol_expr is not None:
            cloned.expr_tol_expr = _inline_user_function_expr(
                cloned.expr_tol_expr, functions
            )
        return cloned
    if isinstance(event, AstCombinedEvent):
        return AstCombinedEvent(
            [_inline_user_function_event(child, functions) for child in event.events]
        )
    return copy.deepcopy(event)


def _inline_user_function_stmt(stmt: Any, functions: Mapping[str, Any]) -> Any:
    if not functions:
        return stmt
    if isinstance(stmt, AstBlock):
        return AstBlock([
            _inline_user_function_stmt(child, functions) for child in stmt.statements
        ])
    if isinstance(stmt, AstAssignment):
        return AstAssignment(
            copy.deepcopy(stmt.target),
            _inline_user_function_expr(stmt.value, functions),
        )
    if isinstance(stmt, AstContribution):
        return AstContribution(
            copy.deepcopy(stmt.branch),
            _inline_user_function_expr(stmt.expr, functions),
        )
    if isinstance(stmt, AstEventStatement):
        return AstEventStatement(
            _inline_user_function_event(stmt.event, functions),
            _inline_user_function_stmt(stmt.body, functions),
        )
    if isinstance(stmt, AstIfStatement):
        return AstIfStatement(
            _inline_user_function_expr(stmt.cond, functions),
            _inline_user_function_stmt(stmt.then_body, functions),
            (
                _inline_user_function_stmt(stmt.else_body, functions)
                if stmt.else_body is not None
                else None
            ),
        )
    if isinstance(stmt, AstForStatement):
        return AstForStatement(
            _inline_user_function_stmt(stmt.init, functions),
            _inline_user_function_expr(stmt.cond, functions),
            _inline_user_function_stmt(stmt.update, functions),
            _inline_user_function_stmt(stmt.body, functions),
        )
    if isinstance(stmt, AstWhileStatement):
        return AstWhileStatement(
            _inline_user_function_expr(stmt.cond, functions),
            _inline_user_function_stmt(stmt.body, functions),
        )
    if isinstance(stmt, AstCaseStatement):
        return AstCaseStatement(
            _inline_user_function_expr(stmt.expr, functions),
            [
                AstCaseItem(
                    [
                        _inline_user_function_expr(value, functions)
                        for value in item.values
                    ],
                    _inline_user_function_stmt(item.body, functions),
                )
                for item in stmt.items
            ],
        )
    if isinstance(stmt, AstSystemTask):
        return AstSystemTask(
            stmt.name,
            [_inline_user_function_expr(arg, functions) for arg in stmt.args],
        )
    if isinstance(stmt, AstTaskCall):
        return AstTaskCall(
            stmt.name,
            [_inline_user_function_expr(arg, functions) for arg in stmt.args],
        )
    return copy.deepcopy(stmt)


def _lower_module_body_with_user_function_inlining(module: Any) -> Optional[BlockIR]:
    analog_block = getattr(module, "analog_block", None)
    body_ast = getattr(analog_block, "body", None)
    if body_ast is None:
        return None
    body_ir = lower_stmt(
        _inline_user_function_stmt(body_ast, _simple_user_function_map(module))
    )
    return body_ir if isinstance(body_ir, BlockIR) else None


def _lower_module_body_for_model(module: Any, model: Any) -> Optional[BlockIR]:
    body_ir = _lower_module_body_with_user_function_inlining(module)
    if not isinstance(body_ir, BlockIR):
        return body_ir
    rewritten = _replace_model_query_stmt(body_ir, model)
    return rewritten if isinstance(rewritten, BlockIR) else None


def _replace_model_query_stmt(stmt_ir: object, model: Any) -> object:
    if isinstance(stmt_ir, AssignmentIR):
        return AssignmentIR(
            _replace_model_query_assignment_target(stmt_ir.target, model),
            _replace_model_query_expr(stmt_ir.value, model),
        )
    if isinstance(stmt_ir, ContributionIR):
        return ContributionIR(
            _replace_model_query_branch(stmt_ir.branch, model),
            _replace_model_query_expr(stmt_ir.expr, model),
        )
    if isinstance(stmt_ir, EventStatementIR):
        return EventStatementIR(
            _replace_model_query_event(stmt_ir.event, model),
            _replace_model_query_stmt(stmt_ir.body, model),
        )
    if isinstance(stmt_ir, BlockIR):
        return BlockIR(
            tuple(_replace_model_query_stmt(child, model) for child in stmt_ir.statements)
        )
    if isinstance(stmt_ir, IfStatementIR):
        return IfStatementIR(
            _replace_model_query_expr(stmt_ir.cond, model),
            _replace_model_query_stmt(stmt_ir.then_body, model),
            (
                None
                if stmt_ir.else_body is None
                else _replace_model_query_stmt(stmt_ir.else_body, model)
            ),
        )
    if isinstance(stmt_ir, ForStatementIR):
        return ForStatementIR(
            _replace_model_query_stmt(stmt_ir.init, model),
            _replace_model_query_expr(stmt_ir.cond, model),
            _replace_model_query_stmt(stmt_ir.update, model),
            _replace_model_query_stmt(stmt_ir.body, model),
        )
    if isinstance(stmt_ir, WhileStatementIR):
        return WhileStatementIR(
            _replace_model_query_expr(stmt_ir.cond, model),
            _replace_model_query_stmt(stmt_ir.body, model),
        )
    if isinstance(stmt_ir, CaseStatementIR):
        return CaseStatementIR(
            _replace_model_query_expr(stmt_ir.expr, model),
            tuple(
                CaseItemIR(
                    tuple(
                        _replace_model_query_expr(value, model)
                        for value in item.values
                    ),
                    _replace_model_query_stmt(item.body, model),
                )
                for item in stmt_ir.items
            ),
        )
    if isinstance(stmt_ir, SystemTaskIR):
        return SystemTaskIR(
            stmt_ir.name,
            tuple(_replace_model_query_expr(arg, model) for arg in stmt_ir.args),
        )
    return stmt_ir


def _replace_model_query_assignment_target(target: object, model: Any) -> object:
    if isinstance(target, ArrayAccessIR):
        return ArrayAccessIR(
            target.name,
            _replace_model_query_expr(target.index, model),
        )
    return target


def _declared_branch_map(model: Any) -> dict[str, tuple[str, str]]:
    module = getattr(getattr(model, "__class__", type(model)), "_module_ast", None)
    result: dict[str, tuple[str, str]] = {}
    for branch in getattr(module, "branches", ()) or ():
        name = str(getattr(branch, "name", ""))
        node1 = str(getattr(branch, "node1", ""))
        node2 = str(getattr(branch, "node2", ""))
        if name and node1 and node2:
            result[name] = (node1, node2)
    return result


def _resolve_oomr_node_path(path: Any) -> str:
    target = str(path).strip()
    for prefix in ("$root.", "$root/"):
        if target.startswith(prefix):
            target = target[len(prefix) :]
            break
    if target == "$root":
        return target
    if target.startswith("$root"):
        target = target[len("$root") :].lstrip("./")
    return target.replace("/", ".").split(".")[-1]


def _resolve_model_string_expr(model: Any, expr_ir: ExprIR) -> Optional[str]:
    if isinstance(expr_ir, LiteralIR) and isinstance(expr_ir.value, str):
        return str(expr_ir.value)
    if isinstance(expr_ir, IdentifierIR):
        name = str(expr_ir.name)
        value = (getattr(model, "params", {}) or {}).get(name)
        if isinstance(value, str):
            return value
        value = (getattr(model, "state", {}) or {}).get(name)
        if isinstance(value, str):
            return value
    return None


def _resolve_model_string_ast(model: Any, expr: object) -> Optional[str]:
    if isinstance(expr, AstStringLiteral):
        return str(expr.value)
    if isinstance(expr, AstIdentifier):
        name = str(expr.name)
        value = (getattr(model, "params", {}) or {}).get(name)
        if isinstance(value, str):
            return value
        value = (getattr(model, "state", {}) or {}).get(name)
        if isinstance(value, str):
            return value
    return None


def _resolve_branch_node_name(model: Any, node: str) -> str:
    aliases = _analog_node_alias_map(model)
    if node in aliases:
        return aliases[node]
    value = (getattr(model, "params", {}) or {}).get(str(node))
    if isinstance(value, str):
        return _resolve_oomr_node_path(value)
    value = (getattr(model, "state", {}) or {}).get(str(node))
    if isinstance(value, str):
        return _resolve_oomr_node_path(value)
    return str(node)


def _analog_node_alias_map(model: Any) -> dict[str, str]:
    module = getattr(getattr(model, "__class__", type(model)), "_module_ast", None)
    result: dict[str, str] = {}
    body = getattr(getattr(module, "analog_block", None), "body", None)
    for call in _iter_ast_analog_node_alias_calls(body):
        args = tuple(getattr(call, "args", ()) or ())
        if len(args) < 2:
            continue
        local = _resolve_alias_local_node(args[0])
        target = _resolve_model_string_ast(model, args[1])
        if local is None:
            continue
        if target is None:
            target = _resolve_alias_local_node(args[1])
        if target is None:
            continue
        resolved_target = _resolve_oomr_node_path(target)
        if local and resolved_target and resolved_target != "$root":
            result[local] = resolved_target
    return result


def _iter_ast_analog_node_alias_calls(stmt: object):
    for child in _iter_ast_statement_tree(stmt):
        if isinstance(child, (AstSystemTask, AstTaskCall)):
            if str(getattr(child, "name", "")) == "$analog_node_alias":
                yield child
        for expr in _iter_ast_exprs_from_stmt(child):
            if isinstance(expr, AstFunctionCall) and str(expr.name) == "$analog_node_alias":
                yield expr


def _resolve_alias_local_node(expr: object) -> Optional[str]:
    if isinstance(expr, AstIdentifier):
        return _resolve_oomr_node_path(expr.name)
    if isinstance(expr, AstStringLiteral):
        return _resolve_oomr_node_path(expr.value)
    return None


def _replace_model_query_branch(branch: BranchAccessIR, model: Any) -> BranchAccessIR:
    node1 = _resolve_branch_node_name(model, branch.node1)
    node2 = (
        None
        if branch.node2 is None
        else _resolve_branch_node_name(model, branch.node2)
    )
    if (
        branch.node2 is None
        and branch.node1_index is None
        and branch.node1_index2 is None
        and branch.node2_index is None
        and branch.node2_index2 is None
    ):
        declared = _declared_branch_map(model).get(str(branch.node1))
        if declared is not None:
            node1, node2 = declared
    return BranchAccessIR(
        access_type=branch.access_type,
        node1=node1,
        node2=node2,
        node1_index=(
            None
            if branch.node1_index is None
            else _replace_model_query_expr(branch.node1_index, model)
        ),
        node2_index=(
            None
            if branch.node2_index is None
            else _replace_model_query_expr(branch.node2_index, model)
        ),
        node1_index2=(
            None
            if branch.node1_index2 is None
            else _replace_model_query_expr(branch.node1_index2, model)
        ),
        node2_index2=(
            None
            if branch.node2_index2 is None
            else _replace_model_query_expr(branch.node2_index2, model)
        ),
    )


def _replace_model_query_event(event_ir: object, model: Any) -> object:
    if isinstance(event_ir, EventTriggerIR):
        return EventTriggerIR(
            event_type=event_ir.event_type,
            args=tuple(_replace_model_query_expr(arg, model) for arg in event_ir.args),
            direction=event_ir.direction,
            time_tol=(
                None
                if event_ir.time_tol is None
                else _replace_model_query_expr(event_ir.time_tol, model)
            ),
            expr_tol=(
                None
                if event_ir.expr_tol is None
                else _replace_model_query_expr(event_ir.expr_tol, model)
            ),
        )
    if isinstance(event_ir, CombinedEventIR):
        return CombinedEventIR(
            tuple(_replace_model_query_event(child, model) for child in event_ir.events)
        )
    return event_ir


def _replace_model_query_expr(expr_ir: ExprIR, model: Any) -> ExprIR:
    if isinstance(expr_ir, IdentifierIR) and expr_ir.name == "$mfactor":
        return LiteralIR(float(getattr(model, "_mfactor_value", 1.0)))
    if isinstance(expr_ir, FunctionCallIR):
        name = str(expr_ir.name)
        if name == "$param_given":
            query_name = _query_arg_name(expr_ir.args[0]) if expr_ir.args else None
            if query_name is not None:
                given = getattr(model, "_given_params", set()) or set()
                return LiteralIR(
                    1.0 if str(query_name).strip().lower() in given else 0.0
                )
        if name == "$port_connected":
            query_name = _query_arg_name(expr_ir.args[0]) if expr_ir.args else None
            if query_name is not None:
                return LiteralIR(1.0 if _model_port_connected(model, query_name) else 0.0)
        if name == "$mfactor" and not expr_ir.args:
            return LiteralIR(float(getattr(model, "_mfactor_value", 1.0)))
        if name == "$analog_node_alias" and len(expr_ir.args) >= 2:
            return LiteralIR(1.0)
        rewritten_args = tuple(_replace_model_query_expr(arg, model) for arg in expr_ir.args)
        if name == "$table_model":
            rewritten_table = _rewrite_table_model_call(
                FunctionCallIR(expr_ir.name, rewritten_args),
                model,
            )
            if rewritten_table is not None:
                return rewritten_table
        return FunctionCallIR(
            expr_ir.name,
            rewritten_args,
        )
    if isinstance(expr_ir, ArrayAccessIR):
        return ArrayAccessIR(
            expr_ir.name,
            _replace_model_query_expr(expr_ir.index, model),
        )
    if isinstance(expr_ir, BinaryExprIR):
        return BinaryExprIR(
            expr_ir.op,
            _replace_model_query_expr(expr_ir.left, model),
            _replace_model_query_expr(expr_ir.right, model),
        )
    if isinstance(expr_ir, UnaryExprIR):
        return UnaryExprIR(
            expr_ir.op,
            _replace_model_query_expr(expr_ir.operand, model),
        )
    if isinstance(expr_ir, TernaryExprIR):
        return TernaryExprIR(
            _replace_model_query_expr(expr_ir.cond, model),
            _replace_model_query_expr(expr_ir.true_expr, model),
            _replace_model_query_expr(expr_ir.false_expr, model),
        )
    if isinstance(expr_ir, BranchAccessIR):
        return _replace_model_query_branch(expr_ir, model)
    if isinstance(expr_ir, MethodCallIR):
        return MethodCallIR(
            expr_ir.obj,
            expr_ir.method,
            tuple(_replace_model_query_expr(arg, model) for arg in expr_ir.args),
        )
    return expr_ir


def _rewrite_table_model_call(
    expr_ir: FunctionCallIR,
    model: Any,
) -> Optional[ExprIR]:
    args = tuple(expr_ir.args)
    if len(args) >= 5 and all(isinstance(arg, IdentifierIR) for arg in args[2:5]):
        return _rewrite_table_model_2d(args, model)
    if len(args) >= 2:
        return _rewrite_table_model_1d(args, model)
    return None


def _rewrite_table_model_1d(
    args: Tuple[ExprIR, ...],
    model: Any,
) -> Optional[ExprIR]:
    filename = _resolve_model_string_expr(model, args[1])
    if filename is None:
        return None
    points = _load_table_model_1d_points(filename)
    if not points:
        return None
    return _piecewise_linear_1d_expr(args[0], points)


def _rewrite_table_model_2d(
    args: Tuple[ExprIR, ...],
    model: Any,
) -> Optional[ExprIR]:
    arrays = _static_array_literal_values(model)
    x_name = str(args[2].name) if isinstance(args[2], IdentifierIR) else ""
    y_name = str(args[3].name) if isinstance(args[3], IdentifierIR) else ""
    z_name = str(args[4].name) if isinstance(args[4], IdentifierIR) else ""
    xvals = arrays.get(x_name, ())
    yvals = arrays.get(y_name, ())
    zvals = arrays.get(z_name, ())
    n = min(len(xvals), len(yvals), len(zvals))
    if n <= 0:
        return None
    points = tuple((float(xvals[i]), float(yvals[i]), float(zvals[i])) for i in range(n))
    plane = _fit_plane(points)
    if plane is not None:
        a, b, c = plane
        return _add(_add(_mul(_lit(a), args[0]), _mul(_lit(b), args[1])), _lit(c))
    return _bilinear_rect_expr(args[0], args[1], points)


def _load_table_model_1d_points(filename: str) -> Tuple[Tuple[float, float], ...]:
    path = Path(filename)
    if not path.exists():
        path = Path.cwd() / filename
    if not path.exists() or not path.is_file():
        return ()
    rows: list[tuple[float, float]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            tokens = stripped.replace(",", " ").split()
            if len(tokens) < 2:
                continue
            try:
                rows.append((float(tokens[0]), float(tokens[1])))
            except ValueError:
                continue
    except OSError:
        return ()
    return tuple(sorted(rows, key=lambda item: item[0]))


def _static_array_literal_values(model: Any) -> dict[str, Tuple[float, ...]]:
    module = getattr(getattr(model, "__class__", type(model)), "_module_ast", None)
    body = getattr(getattr(module, "analog_block", None), "body", None)
    by_name: dict[str, dict[int, float]] = {}
    for stmt in _iter_ast_statement_tree(body):
        if not isinstance(stmt, AstAssignment):
            continue
        target = getattr(stmt, "target", None)
        value = getattr(stmt, "value", None)
        if not isinstance(target, AstArrayAccess):
            continue
        idx = _number_literal_value(getattr(target, "index", None))
        val = _number_literal_value(value)
        if idx is None or val is None:
            continue
        by_name.setdefault(str(target.name), {})[int(idx)] = float(val)
    return {
        name: tuple(values[idx] for idx in sorted(values))
        for name, values in by_name.items()
    }


def _iter_ast_statement_tree(stmt: object):
    if stmt is None:
        return
    yield stmt
    if isinstance(stmt, AstBlock):
        for child in getattr(stmt, "statements", ()) or ():
            yield from _iter_ast_statement_tree(child)
        return
    if isinstance(stmt, AstEventStatement):
        yield from _iter_ast_statement_tree(getattr(stmt, "body", None))
        return
    if isinstance(stmt, AstIfStatement):
        yield from _iter_ast_statement_tree(getattr(stmt, "then_body", None))
        yield from _iter_ast_statement_tree(getattr(stmt, "else_body", None))
        return
    if isinstance(stmt, AstForStatement):
        yield from _iter_ast_statement_tree(getattr(stmt, "init", None))
        yield from _iter_ast_statement_tree(getattr(stmt, "update", None))
        yield from _iter_ast_statement_tree(getattr(stmt, "body", None))
        return
    if isinstance(stmt, AstWhileStatement):
        yield from _iter_ast_statement_tree(getattr(stmt, "body", None))
        return
    if isinstance(stmt, AstCaseStatement):
        for item in getattr(stmt, "items", ()) or ():
            yield from _iter_ast_statement_tree(getattr(item, "body", None))


def _iter_ast_exprs_from_stmt(stmt: object):
    if isinstance(stmt, AstAssignment):
        yield from _iter_ast_expr_tree(getattr(stmt, "target", None))
        yield from _iter_ast_expr_tree(getattr(stmt, "value", None))
        return
    if isinstance(stmt, AstContribution):
        yield from _iter_ast_expr_tree(getattr(stmt, "branch", None))
        yield from _iter_ast_expr_tree(getattr(stmt, "expr", None))
        return
    if isinstance(stmt, AstEventStatement):
        event = getattr(stmt, "event", None)
        for arg in getattr(event, "args", ()) or ():
            yield from _iter_ast_expr_tree(arg)
        yield from _iter_ast_expr_tree(getattr(event, "time_tol_expr", None))
        yield from _iter_ast_expr_tree(getattr(event, "expr_tol_expr", None))
        return
    if isinstance(stmt, AstIfStatement):
        yield from _iter_ast_expr_tree(getattr(stmt, "cond", None))
        return
    if isinstance(stmt, AstForStatement):
        yield from _iter_ast_expr_tree(getattr(stmt, "cond", None))
        return
    if isinstance(stmt, AstWhileStatement):
        yield from _iter_ast_expr_tree(getattr(stmt, "cond", None))
        return
    if isinstance(stmt, AstCaseStatement):
        yield from _iter_ast_expr_tree(getattr(stmt, "expr", None))
        for item in getattr(stmt, "items", ()) or ():
            for value in getattr(item, "values", ()) or ():
                yield from _iter_ast_expr_tree(value)
        return
    if isinstance(stmt, (AstSystemTask, AstTaskCall)):
        for arg in getattr(stmt, "args", ()) or ():
            yield from _iter_ast_expr_tree(arg)


def _iter_ast_expr_tree(expr: object):
    if expr is None:
        return
    yield expr
    if isinstance(expr, AstArrayAccess):
        yield from _iter_ast_expr_tree(getattr(expr, "index", None))
        yield from _iter_ast_expr_tree(getattr(expr, "index2", None))
        return
    if isinstance(expr, AstBranchAccess):
        for child_name in (
            "node1_index",
            "node1_index2",
            "node2_index",
            "node2_index2",
        ):
            yield from _iter_ast_expr_tree(getattr(expr, child_name, None))
        return
    if isinstance(expr, AstBinaryExpr):
        yield from _iter_ast_expr_tree(getattr(expr, "left", None))
        yield from _iter_ast_expr_tree(getattr(expr, "right", None))
        return
    if isinstance(expr, AstUnaryExpr):
        yield from _iter_ast_expr_tree(getattr(expr, "operand", None))
        return
    if isinstance(expr, AstTernaryExpr):
        yield from _iter_ast_expr_tree(getattr(expr, "cond", None))
        yield from _iter_ast_expr_tree(getattr(expr, "true_expr", None))
        yield from _iter_ast_expr_tree(getattr(expr, "false_expr", None))
        return
    if isinstance(expr, (AstFunctionCall, AstMethodCall)):
        for arg in getattr(expr, "args", ()) or ():
            yield from _iter_ast_expr_tree(arg)


def _number_literal_value(expr: object) -> Optional[float]:
    if isinstance(expr, AstNumberLiteral):
        return float(expr.value)
    return None


def _piecewise_linear_1d_expr(
    x_expr: ExprIR,
    points: Tuple[Tuple[float, float], ...],
) -> ExprIR:
    if len(points) == 1:
        return _lit(points[0][1])
    result: ExprIR = _lit(points[-1][1])
    for left, right in reversed(tuple(zip(points, points[1:]))):
        x0, y0 = left
        x1, y1 = right
        result = TernaryExprIR(
            _le(x_expr, _lit(x1)),
            _linear_segment_expr(x_expr, x0, y0, x1, y1),
            result,
        )
    return TernaryExprIR(_le(x_expr, _lit(points[0][0])), _lit(points[0][1]), result)


def _linear_segment_expr(
    x_expr: ExprIR,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> ExprIR:
    if abs(x1 - x0) <= 1e-30:
        return _lit(y1)
    slope = (y1 - y0) / (x1 - x0)
    return _add(_lit(y0), _mul(_sub(x_expr, _lit(x0)), _lit(slope)))


def _fit_plane(points: Tuple[Tuple[float, float, float], ...]) -> Optional[Tuple[float, float, float]]:
    n = len(points)
    for i in range(n):
        x1, y1, z1 = points[i]
        for j in range(i + 1, n):
            x2, y2, z2 = points[j]
            for k in range(j + 1, n):
                x3, y3, z3 = points[k]
                denom = x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)
                if abs(denom) <= 1e-30:
                    continue
                a = (z1 * (y2 - y3) + z2 * (y3 - y1) + z3 * (y1 - y2)) / denom
                b = (x1 * (z2 - z3) + x2 * (z3 - z1) + x3 * (z1 - z2)) / denom
                c = (
                    x1 * (y3 * z2 - y2 * z3)
                    + x2 * (y1 * z3 - y3 * z1)
                    + x3 * (y2 * z1 - y1 * z2)
                ) / denom
                if all(abs(a * x + b * y + c - z) <= 1e-9 for x, y, z in points):
                    return a, b, c
    return None


def _bilinear_rect_expr(
    x_expr: ExprIR,
    y_expr: ExprIR,
    points: Tuple[Tuple[float, float, float], ...],
) -> Optional[ExprIR]:
    ux = sorted({x for x, _y, _z in points})
    uy = sorted({y for _x, y, _z in points})
    if len(ux) != 2 or len(uy) != 2:
        return None
    x0, x1 = ux
    y0, y1 = uy
    if abs(x1 - x0) <= 1e-30 or abs(y1 - y0) <= 1e-30:
        return None
    zmap = {(x, y): z for x, y, z in points}
    if not all((x, y) in zmap for x in ux for y in uy):
        return None
    tx = _div(_sub(x_expr, _lit(x0)), _lit(x1 - x0))
    ty = _div(_sub(y_expr, _lit(y0)), _lit(y1 - y0))
    one_minus_tx = _sub(_lit(1.0), tx)
    one_minus_ty = _sub(_lit(1.0), ty)
    z00 = _lit(zmap[(x0, y0)])
    z10 = _lit(zmap[(x1, y0)])
    z01 = _lit(zmap[(x0, y1)])
    z11 = _lit(zmap[(x1, y1)])
    return _add(
        _add(_mul(z00, _mul(one_minus_tx, one_minus_ty)), _mul(z10, _mul(tx, one_minus_ty))),
        _add(_mul(z01, _mul(one_minus_tx, ty)), _mul(z11, _mul(tx, ty))),
    )


def _lit(value: float) -> LiteralIR:
    return LiteralIR(float(value))


def _add(left: ExprIR, right: ExprIR) -> BinaryExprIR:
    return BinaryExprIR("+", left, right)


def _sub(left: ExprIR, right: ExprIR) -> BinaryExprIR:
    return BinaryExprIR("-", left, right)


def _mul(left: ExprIR, right: ExprIR) -> BinaryExprIR:
    return BinaryExprIR("*", left, right)


def _div(left: ExprIR, right: ExprIR) -> BinaryExprIR:
    return BinaryExprIR("/", left, right)


def _le(left: ExprIR, right: ExprIR) -> BinaryExprIR:
    return BinaryExprIR("<=", left, right)


def _query_arg_name(expr_ir: ExprIR) -> Optional[str]:
    if isinstance(expr_ir, IdentifierIR):
        return expr_ir.name
    if isinstance(expr_ir, LiteralIR) and isinstance(expr_ir.value, str):
        return str(expr_ir.value)
    return None


def _model_port_connected(model: Any, name: str) -> bool:
    port = str(name)
    node_map = getattr(model, "node_map", {}) or {}
    if port in node_map:
        return True
    folded = port.casefold()
    if any(str(key).casefold() == folded for key in node_map):
        return True
    module_ports = getattr(getattr(model, "__class__", type(model)), "_module_ports", ())
    return any(str(candidate).casefold() == folded for candidate in module_ports)


def _iter_ast_block_contributions(stmt: Any):
    if isinstance(stmt, AstBlock):
        for child in stmt.statements:
            yield from _iter_ast_block_contributions(child)
        return
    if isinstance(stmt, AstContribution):
        yield stmt


def _collect_branch_current_idt_raw_ops(model_cls: Any) -> tuple[tuple[Any, ...], ...]:
    module = getattr(model_cls, "_module_ast", None)
    analog_block = getattr(module, "analog_block", None)
    body = getattr(analog_block, "body", None)
    if body is None:
        return ()
    ops: list[tuple[Any, ...]] = []
    for stmt in _iter_ast_block_contributions(body):
        branch = stmt.branch
        if not _is_plain_branch_access(branch, "V"):
            continue
        matched = _match_scaled_idt_call(stmt.expr)
        if matched is None:
            continue
        gain_expr, idt_call = matched
        args = list(getattr(idt_call, "args", ()) or ())
        if not args:
            continue
        current = args[0]
        if not _is_plain_branch_access(current, "I") or current.node2 is None:
            continue
        ic_expr = args[1] if len(args) > 1 else _ast_number(0.0)
        ops.append(
            (
                branch.node1,
                branch.node2,
                current.node1,
                current.node2,
                gain_expr,
                ic_expr,
            )
        )
    return tuple(ops)


def _model_has_branch_current_idt_ops(model_cls: Any) -> bool:
    return bool(_collect_branch_current_idt_raw_ops(model_cls))


def _same_ast_voltage_target(expr: Any, target: Any) -> bool:
    return (
        _is_plain_branch_access(expr, "V")
        and _is_plain_branch_access(target, "V")
        and expr.node1 == target.node1
        and expr.node2 == target.node2
    )


def _same_ast_ddt_target(expr: Any, target: Any) -> bool:
    return (
        isinstance(expr, AstFunctionCall)
        and str(expr.name).lower() == "ddt"
        and len(getattr(expr, "args", ()) or ()) == 1
        and _same_ast_voltage_target(expr.args[0], target)
    )


def _solve_simple_ast_indirect_form(
    target: Any,
    lhs: Any,
    rhs: Any,
    *,
    target_match,
) -> Optional[Any]:
    if target_match(lhs, target):
        return rhs
    if isinstance(lhs, AstBinaryExpr):
        left_is_target = target_match(lhs.left, target)
        right_is_target = target_match(lhs.right, target)
        if lhs.op == "-" and left_is_target:
            return AstBinaryExpr("+", rhs, lhs.right)
        if lhs.op == "-" and right_is_target:
            return AstBinaryExpr("-", lhs.left, rhs)
        if lhs.op == "+" and (left_is_target or right_is_target):
            other = lhs.right if left_is_target else lhs.left
            return AstBinaryExpr("-", rhs, other)
    return None


def _indirect_branch_ddt_rhs_ast(target: Any, lhs: Any, rhs: Any) -> Optional[Any]:
    value = _solve_simple_ast_indirect_form(
        target,
        lhs,
        rhs,
        target_match=_same_ast_ddt_target,
    )
    if value is not None:
        return value
    return _solve_simple_ast_indirect_form(
        target,
        rhs,
        lhs,
        target_match=_same_ast_ddt_target,
    )


def _match_first_order_balance_rhs(
    expr: Any,
    target: Any,
) -> Optional[tuple[AstBranchAccess, Any]]:
    if not isinstance(expr, AstBinaryExpr) or expr.op != "/":
        return None
    numerator = expr.left
    tau_expr = expr.right
    if (
        isinstance(numerator, AstBinaryExpr)
        and numerator.op == "-"
        and _is_plain_branch_access(numerator.left, "V")
        and _same_ast_voltage_target(numerator.right, target)
    ):
        return numerator.left, tau_expr
    return None


def _iter_ast_block_task_calls(stmt: Any):
    if isinstance(stmt, AstBlock):
        for child in stmt.statements:
            yield from _iter_ast_block_task_calls(child)
        return
    if isinstance(stmt, AstTaskCall):
        yield stmt


def _collect_indirect_branch_ode_raw_ops(model_cls: Any) -> tuple[tuple[Any, ...], ...]:
    module = getattr(model_cls, "_module_ast", None)
    analog_block = getattr(module, "analog_block", None)
    body = getattr(analog_block, "body", None)
    if body is None:
        return ()
    ops: list[tuple[Any, ...]] = []
    for stmt in _iter_ast_block_task_calls(body):
        if str(getattr(stmt, "name", "")) != "$indirect_branch":
            continue
        args = list(getattr(stmt, "args", ()) or ())
        if len(args) < 3:
            continue
        target, lhs, rhs = args[:3]
        if not _is_plain_branch_access(target, "V"):
            continue
        derivative_rhs = _indirect_branch_ddt_rhs_ast(target, lhs, rhs)
        if derivative_rhs is None:
            continue
        matched = _match_first_order_balance_rhs(derivative_rhs, target)
        if matched is None:
            continue
        input_branch, tau_expr = matched
        if not _is_plain_branch_access(input_branch, "V") or input_branch.node2 is not None:
            continue
        ops.append((target.node1, target.node2, input_branch.node1, tau_expr))
    return tuple(ops)


def _model_has_indirect_branch_ode_ops(model_cls: Any) -> bool:
    return bool(_collect_indirect_branch_ode_raw_ops(model_cls))


def _collect_branch_current_ddt_raw_ops(model_cls: Any) -> tuple[tuple[Any, ...], ...]:
    module = getattr(model_cls, "_module_ast", None)
    analog_block = getattr(module, "analog_block", None)
    body = getattr(analog_block, "body", None)
    if body is None:
        return ()
    ops: list[tuple[Any, ...]] = []
    for stmt in _iter_ast_block_contributions(body):
        branch = stmt.branch
        if not _is_plain_branch_access(branch, "I") or branch.node2 is None:
            continue
        matched = _match_scaled_ddt_call(stmt.expr)
        if matched is None:
            continue
        gain_expr, ddt_call = matched
        args = list(getattr(ddt_call, "args", ()) or ())
        if len(args) != 1:
            continue
        voltage = args[0]
        if not _is_plain_branch_access(voltage, "V") or voltage.node2 is None:
            continue
        ops.append((branch.node1, branch.node2, voltage.node1, voltage.node2, gain_expr))
    return tuple(ops)


def _model_has_branch_current_ddt_ops(model_cls: Any) -> bool:
    return bool(_collect_branch_current_ddt_raw_ops(model_cls))


def _convert_branch_current_idt_ops(
    *,
    model: Any,
    model_index: int,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
) -> tuple[Tuple[RustSimBranchIdtOp, ...], Tuple[str, ...]]:
    model_cls = getattr(model, "__class__", type(model))
    raw_ops = _collect_branch_current_idt_raw_ops(model_cls)
    if not raw_ops:
        return (), ()

    converted: list[RustSimBranchIdtOp] = []
    reasons: list[str] = []
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    for op_index, (target, reference, current_p, current_n, gain_expr, ic_expr) in enumerate(raw_ops):
        gain, gain_reason = _scalar(model, _ast_static_scalar_expr(gain_expr))
        ic, ic_reason = _scalar(model, _ast_static_scalar_expr(ic_expr))
        if gain_reason is not None or gain is None:
            reasons.append(f"{prefix}:branch_idt:{op_index}:gain:{gain_reason}")
            continue
        if ic_reason is not None or ic is None:
            reasons.append(f"{prefix}:branch_idt:{op_index}:ic:{ic_reason}")
            continue
        if not math.isfinite(float(gain)) or not math.isfinite(float(ic)):
            reasons.append(f"{prefix}:branch_idt:{op_index}:nonfinite_gain_or_ic")
            continue

        target_id = _add_node(_external_node(model, str(target)), node_ids, nodes)
        reference_id = (
            None
            if reference is None
            else _add_node(_external_node(model, str(reference)), node_ids, nodes)
        )
        current_node = _branch_current_node_name(
            _external_node(model, str(current_p)),
            _external_node(model, str(current_n)),
        )
        input_id = _add_node(current_node, node_ids, nodes)
        state_name = f"$branch_idt_{op_index}"
        state_key = (model_index, state_name)
        if state_key not in state_ids:
            state_id = len(state_ids)
            state_ids[state_key] = state_id
            states.append(
                RustSimState(
                    name=f"{model_index}:{state_name}",
                    state_id=state_id,
                    initial_value=float(ic),
                    is_integer=False,
                )
            )
        converted.append(
            RustSimBranchIdtOp(
                target_node_id=target_id,
                reference_node_id=reference_id,
                input_node_id=input_id,
                state_id=state_ids[state_key],
                gain=float(gain),
                ic=float(ic),
            )
        )

    return tuple(converted), tuple(reasons)


def _convert_branch_current_ddt_ops(
    *,
    model: Any,
    model_index: int,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
) -> tuple[Tuple[RustSimBranchDdtOp, ...], Tuple[str, ...]]:
    model_cls = getattr(model, "__class__", type(model))
    raw_ops = _collect_branch_current_ddt_raw_ops(model_cls)
    if not raw_ops:
        return (), ()

    converted: list[RustSimBranchDdtOp] = []
    reasons: list[str] = []
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    for op_index, (current_p, current_n, voltage_p, voltage_n, gain_expr) in enumerate(raw_ops):
        gain, gain_reason = _scalar(model, _ast_static_scalar_expr(gain_expr))
        if gain_reason is not None or gain is None:
            reasons.append(f"{prefix}:branch_ddt:{op_index}:gain:{gain_reason}")
            continue
        if not math.isfinite(float(gain)):
            reasons.append(f"{prefix}:branch_ddt:{op_index}:nonfinite_gain")
            continue

        current_node = _branch_current_node_name(
            _external_node(model, str(current_p)),
            _external_node(model, str(current_n)),
        )
        current_id = _add_node(current_node, node_ids, nodes)
        pos_id = _add_node(_external_node(model, str(voltage_p)), node_ids, nodes)
        neg_id = _add_node(_external_node(model, str(voltage_n)), node_ids, nodes)
        state_name = f"$branch_ddt_{op_index}"
        state_key = (model_index, state_name)
        if state_key not in state_ids:
            state_id = len(state_ids)
            state_ids[state_key] = state_id
            states.append(
                RustSimState(
                    name=f"{model_index}:{state_name}",
                    state_id=state_id,
                    initial_value=0.0,
                    is_integer=False,
                )
            )
        converted.append(
            RustSimBranchDdtOp(
                current_node_id=current_id,
                pos_node_id=pos_id,
                neg_node_id=neg_id,
                state_id=state_ids[state_key],
                gain=float(gain),
            )
        )

    return tuple(converted), tuple(reasons)


def _convert_indirect_branch_ode_ops(
    *,
    model: Any,
    model_index: int,
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
) -> tuple[Tuple[RustSimIndirectBranchOdeOp, ...], Tuple[str, ...]]:
    model_cls = getattr(model, "__class__", type(model))
    raw_ops = _collect_indirect_branch_ode_raw_ops(model_cls)
    if not raw_ops:
        return (), ()

    converted: list[RustSimIndirectBranchOdeOp] = []
    reasons: list[str] = []
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    for op_index, (target, reference, input_node, tau_expr) in enumerate(raw_ops):
        tau, tau_reason = _scalar(model, _ast_static_scalar_expr(tau_expr))
        if tau_reason is not None or tau is None:
            reasons.append(f"{prefix}:indirect_ode:{op_index}:tau:{tau_reason}")
            continue
        if not math.isfinite(float(tau)) or float(tau) <= 0.0:
            reasons.append(f"{prefix}:indirect_ode:{op_index}:invalid_tau")
            continue

        target_id = _add_node(_external_node(model, str(target)), node_ids, nodes)
        reference_id = (
            None
            if reference is None
            else _add_node(_external_node(model, str(reference)), node_ids, nodes)
        )
        input_id = _add_node(_external_node(model, str(input_node)), node_ids, nodes)
        state_name = f"$indirect_branch_ode_{op_index}"
        state_key = (model_index, state_name)
        if state_key not in state_ids:
            state_id = len(state_ids)
            state_ids[state_key] = state_id
            states.append(
                RustSimState(
                    name=f"{model_index}:{state_name}",
                    state_id=state_id,
                    initial_value=0.0,
                    is_integer=False,
                )
            )
        converted.append(
            RustSimIndirectBranchOdeOp(
                target_node_id=target_id,
                reference_node_id=reference_id,
                input_node_id=input_id,
                state_id=state_ids[state_key],
                tau=float(tau),
                ic=0.0,
            )
        )

    return tuple(converted), tuple(reasons)


def _convert_event_transition_ops(
    *,
    model: Any,
    model_index: int,
    preapplied_initial_step_file_read: bool = False,
    global_contributed_nodes: frozenset[str],
    node_ids: dict[str, int],
    nodes: list[RustSimNode],
    state_ids: dict[tuple[int, str], int],
    states: list[RustSimState],
    param_ids: dict[tuple[int, str], int],
    params: list[RustSimParam],
    events: list[RustSimEvent],
    transitions: list[RustSimTransition],
    slews: list[RustSimSlew],
    body_stmt_ops: list[BodyStmtOp],
    body_expr_ops: list[BodyExprOp],
    side_effects: list[RustSimSideEffect],
) -> Tuple[str, ...]:
    model_cls = getattr(model, "__class__", type(model))
    prefix = f"model:{model_index}:{getattr(model_cls, '__name__', 'unknown')}"
    module = getattr(model_cls, "_module_ast", None)
    if module is None:
        return (f"{prefix}:module_ast_unavailable",)
    body_ir = _lower_module_body_for_model(module, model)
    if not isinstance(body_ir, BlockIR):
        return (f"{prefix}:stmt_lower_failed",)
    if preapplied_initial_step_file_read:
        body_ir = _strip_preapplied_initial_file_read_events(body_ir)
    if _prefer_existing_timer_static_linear_path(
        model_cls
    ) and not _stmt_has_display_strobe(body_ir):
        return (f"{prefix}:timer_static_linear_specialized_path_preferred",)
    bindings = _extend_bindings_from_static_array_accesses(
        build_state_binding_ir(module),
        body_ir,
    )
    bindings = _extend_bindings_with_repeat_loop_slots(bindings, body_ir)
    bindings = _extend_bindings_with_stateful_function_slots(bindings, body_ir)
    local_node_slots, node_slot_to_global = _node_slot_maps(
        model=model,
        bindings=bindings,
        node_ids=node_ids,
        nodes=nodes,
    )
    _extend_node_slots_from_static_branches(
        model=model,
        stmt_ir=body_ir,
        node_ids=node_ids,
        nodes=nodes,
        local_node_slots=local_node_slots,
        node_slot_to_global=node_slot_to_global,
    )
    state_slot_to_global = _ensure_model_state_slots(
        model=model,
        model_index=model_index,
        state_ids=state_ids,
        states=states,
        bindings=bindings,
    )
    param_slot_to_global = _ensure_model_param_slots(
        model=model,
        model_index=model_index,
        param_ids=param_ids,
        params=params,
    )

    reasons: list[str] = []
    converted_events = 0
    converted_always_bodies = 0
    converted_transitions = 0
    converted_slews = 0
    pending_continuous: list[object] = []
    seen_transition = False
    contributed_nodes = _collect_contributed_nodes(body_ir)
    side_effect_slot_offset = len(side_effects)
    side_effect_builder = _RustSimSideEffectBuilder(model)
    phase_contributed_nodes = set(contributed_nodes)
    if global_contributed_nodes:
        for local_name in local_node_slots:
            if _external_node(model, local_name) in global_contributed_nodes:
                phase_contributed_nodes.add(local_name)
    phase_contributed_nodes = frozenset(phase_contributed_nodes)

    def flush_continuous_body(phase: int = EVENT_PHASE_PRE) -> None:
        nonlocal converted_always_bodies, pending_continuous
        if not pending_continuous:
            return
        body_program = encode_body_stmt_ops(
            BlockIR(tuple(pending_continuous)),
            bindings,
            local_node_slots,
            side_effects=side_effect_builder,
        )
        pending_continuous = []
        if body_program is None:
            reasons.append(f"{prefix}:continuous_body_not_lowered")
            return
        body_start, body_count = _append_body_program(
            body_stmt_ops,
            body_expr_ops,
            body_program,
            node_slot_to_global=node_slot_to_global,
            state_slot_to_global=state_slot_to_global,
            param_slot_to_global=param_slot_to_global,
            side_effect_slot_offset=side_effect_slot_offset,
        )
        if body_count <= 0:
            return
        events.append(
            RustSimEvent(
                kind=EVENT_DUE_ALWAYS,
                event_id=len(events),
                phase=phase,
                body_stmt_start=body_start,
                body_stmt_count=body_count,
            )
        )
        converted_always_bodies += 1

    for stmt in body_ir.statements:
        if _is_branch_ddt_contribution_stmt(stmt):
            continue
        if _is_continuous_body_stmt(stmt):
            if seen_transition:
                pending_continuous.append(stmt)
                flush_continuous_body(EVENT_PHASE_POST)
            else:
                pending_continuous.append(stmt)
            continue

        if isinstance(stmt, EventStatementIR):
            flush_continuous_body()
            due_program = encode_event_due_program(stmt.event, bindings, local_node_slots)
            body_program = encode_body_stmt_ops(
                stmt.body,
                bindings,
                local_node_slots,
                side_effects=side_effect_builder,
            )
            if due_program is None:
                reasons.append(f"{prefix}:event_due_not_lowered")
                continue
            if body_program is None:
                reasons.append(f"{prefix}:event_body_not_lowered")
                for tag in classify_body_stmt_ops_rejection(
                    stmt.body,
                    bindings,
                    local_node_slots,
                ):
                    reasons.append(f"{prefix}:event_body:{tag}")
                continue
            trigger_phases = _event_trigger_phases(stmt.event, phase_contributed_nodes)
            if len(trigger_phases) != len(due_program.triggers):
                reasons.append(f"{prefix}:event_phase_trigger_mismatch")
                continue
            body_start, body_count = _append_body_program(
                body_stmt_ops,
                body_expr_ops,
                body_program,
                node_slot_to_global=node_slot_to_global,
                state_slot_to_global=state_slot_to_global,
                param_slot_to_global=param_slot_to_global,
                side_effect_slot_offset=side_effect_slot_offset,
            )
            for trigger, trigger_phase in zip(due_program.triggers, trigger_phases):
                expr_start, expr_count = _append_expr_segment(
                    body_expr_ops,
                    tuple(trigger.expr_ops),
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                time_tol_start, time_tol_count = _append_expr_segment(
                    body_expr_ops,
                    tuple(trigger.time_tol_ops),
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                expr_tol_start, expr_tol_count = _append_expr_segment(
                    body_expr_ops,
                    tuple(trigger.expr_tol_ops),
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                timer_start_expr_start, timer_start_expr_count = _append_expr_segment(
                    body_expr_ops,
                    tuple(trigger.timer_start_ops),
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                timer_period_expr_start, timer_period_expr_count = _append_expr_segment(
                    body_expr_ops,
                    tuple(trigger.timer_period_ops),
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                events.append(
                    RustSimEvent(
                        kind=str(trigger.kind),
                        event_id=len(events),
                        phase=int(trigger_phase),
                        direction=int(trigger.direction),
                        expr_start=expr_start,
                        expr_count=expr_count,
                        time_tol_start=time_tol_start,
                        time_tol_count=time_tol_count,
                        expr_tol_start=expr_tol_start,
                        expr_tol_count=expr_tol_count,
                        timer_start_expr_start=timer_start_expr_start,
                        timer_start_expr_count=timer_start_expr_count,
                        timer_period_expr_start=timer_period_expr_start,
                        timer_period_expr_count=timer_period_expr_count,
                        body_stmt_start=body_start,
                        body_stmt_count=body_count,
                    )
                )
                converted_events += 1
            continue

        if isinstance(stmt, (ContributionIR, ForStatementIR)):
            flush_continuous_body()
            transition_program = encode_transition_contribution_program(
                BlockIR((stmt,)),
                bindings,
                local_node_slots,
            )
            if transition_program is None:
                slew_program = encode_slew_contribution_program(
                    BlockIR((stmt,)),
                    bindings,
                    local_node_slots,
                )
                if slew_program is not None:
                    for idx, output_slot in enumerate(slew_program.output_node_slots):
                        expr_base = idx * 5
                        if expr_base + 4 >= len(slew_program.expr_segments):
                            reasons.append(f"{prefix}:slew_expr_segment_mismatch")
                            continue
                        target_start, target_count = _append_expr_segment(
                            body_expr_ops,
                            slew_program.expr_segments[expr_base],
                            node_slot_to_global=node_slot_to_global,
                            state_slot_to_global=state_slot_to_global,
                            param_slot_to_global=param_slot_to_global,
                        )
                        rise_start, rise_count = _append_expr_segment(
                            body_expr_ops,
                            slew_program.expr_segments[expr_base + 1],
                            node_slot_to_global=node_slot_to_global,
                            state_slot_to_global=state_slot_to_global,
                            param_slot_to_global=param_slot_to_global,
                        )
                        fall_start, fall_count = _append_expr_segment(
                            body_expr_ops,
                            slew_program.expr_segments[expr_base + 2],
                            node_slot_to_global=node_slot_to_global,
                            state_slot_to_global=state_slot_to_global,
                            param_slot_to_global=param_slot_to_global,
                        )
                        output_bias_start, output_bias_count = _append_expr_segment(
                            body_expr_ops,
                            slew_program.expr_segments[expr_base + 3],
                            node_slot_to_global=node_slot_to_global,
                            state_slot_to_global=state_slot_to_global,
                            param_slot_to_global=param_slot_to_global,
                        )
                        output_scale_start, output_scale_count = _append_expr_segment(
                            body_expr_ops,
                            slew_program.expr_segments[expr_base + 4],
                            node_slot_to_global=node_slot_to_global,
                            state_slot_to_global=state_slot_to_global,
                            param_slot_to_global=param_slot_to_global,
                        )
                        reference_slot = slew_program.reference_node_slots[idx]
                        slews.append(
                            RustSimSlew(
                                slew_id=len(slews),
                                output_node_id=int(
                                    node_slot_to_global.get(int(output_slot), output_slot)
                                ),
                                reference_node_id=(
                                    None
                                    if reference_slot is None
                                    else int(
                                        node_slot_to_global.get(
                                            int(reference_slot), reference_slot
                                        )
                                    )
                                ),
                                target_expr_start=target_start,
                                target_expr_count=target_count,
                                rise_expr_start=rise_start,
                                rise_expr_count=rise_count,
                                fall_expr_start=fall_start,
                                fall_expr_count=fall_count,
                                output_bias_expr_start=output_bias_start,
                                output_bias_expr_count=output_bias_count,
                                output_scale_expr_start=output_scale_start,
                                output_scale_expr_count=output_scale_count,
                            )
                        )
                        converted_slews += 1
                    continue
                direct_program = encode_body_stmt_ops(
                    BlockIR((stmt,)),
                    bindings,
                    local_node_slots,
                    side_effects=side_effect_builder,
                )
                if direct_program is not None:
                    body_start, body_count = _append_body_program(
                        body_stmt_ops,
                        body_expr_ops,
                        direct_program,
                        node_slot_to_global=node_slot_to_global,
                        state_slot_to_global=state_slot_to_global,
                        param_slot_to_global=param_slot_to_global,
                        side_effect_slot_offset=side_effect_slot_offset,
                    )
                    if body_count > 0:
                        events.append(
                            RustSimEvent(
                                kind=EVENT_DUE_ALWAYS,
                                event_id=len(events),
                                phase=EVENT_PHASE_PRE,
                                body_stmt_start=body_start,
                                body_stmt_count=body_count,
                            )
                        )
                        converted_always_bodies += 1
                    continue
                reasons.append(f"{prefix}:continuous_contribution_not_lowered")
                continue
            for idx, output_slot in enumerate(transition_program.output_node_slots):
                expr_base = idx * 6
                if expr_base + 5 >= len(transition_program.expr_segments):
                    reasons.append(f"{prefix}:transition_expr_segment_mismatch")
                    continue
                target_start, target_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                delay_start, delay_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base + 1],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                rise_start, rise_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base + 2],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                fall_start, fall_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base + 3],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                output_bias_start, output_bias_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base + 4],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                output_scale_start, output_scale_count = _append_expr_segment(
                    body_expr_ops,
                    transition_program.expr_segments[expr_base + 5],
                    node_slot_to_global=node_slot_to_global,
                    state_slot_to_global=state_slot_to_global,
                    param_slot_to_global=param_slot_to_global,
                )
                reference_slot = transition_program.reference_node_slots[idx]
                transitions.append(
                    RustSimTransition(
                        transition_id=len(transitions),
                        output_node_id=int(node_slot_to_global.get(int(output_slot), output_slot)),
                        reference_node_id=(
                            None
                            if reference_slot is None
                            else int(node_slot_to_global.get(int(reference_slot), reference_slot))
                        ),
                        target_expr_start=target_start,
                        target_expr_count=target_count,
                        delay_expr_start=delay_start,
                        delay_expr_count=delay_count,
                        rise_expr_start=rise_start,
                        rise_expr_count=rise_count,
                        fall_expr_start=fall_start,
                        fall_expr_count=fall_count,
                        output_bias_expr_start=output_bias_start,
                        output_bias_expr_count=output_bias_count,
                        output_scale_expr_start=output_scale_start,
                        output_scale_expr_count=output_scale_count,
                        default_transition=float(
                            getattr(model, "default_transition", 1.0e-12)
                            or 1.0e-12
                        ),
                    )
                )
                converted_transitions += 1
                seen_transition = True
            continue

    flush_continuous_body()

    if (
        converted_events == 0
        and converted_always_bodies == 0
        and converted_transitions == 0
        and converted_slews == 0
    ):
        return (f"{prefix}:no_event_transition_ir",)
    side_effects.extend(
        _remap_side_effect_targets(side_effect_builder.effects, state_slot_to_global)
    )
    return tuple(reasons)


def _source_from_metadata(
    *,
    node: str,
    node_id: int,
    meta: Mapping[str, Any],
    source_data: list[float],
    scale: float = 1.0,
) -> tuple[Optional[RustSimSource], Optional[str]]:
    kind = str(meta.get("kind", ""))
    scale_f = float(scale)
    if kind == SOURCE_DC:
        return (
            RustSimSource(
                node=node,
                node_id=node_id,
                kind=SOURCE_DC,
                params=(scale_f * float(meta.get("voltage", 0.0) or 0.0),),
            ),
            None,
        )
    if kind in (SOURCE_PULSE, SOURCE_SQUARE):
        flags = 0
        if bool(meta.get("has_width", False)):
            flags |= 1
        if bool(meta.get("one_shot", False)):
            flags |= 2
        return (
            RustSimSource(
                node=node,
                node_id=node_id,
                kind=SOURCE_PULSE,
                flags=flags,
                params=(
                    scale_f * float(meta.get("v_lo", 0.0) or 0.0),
                    scale_f * float(meta.get("v_hi", 0.0) or 0.0),
                    float(meta.get("period", 0.0) or 0.0),
                    float(meta.get("duty", 0.5) or 0.5),
                    float(meta.get("rise", 0.0) or 0.0),
                    float(meta.get("fall", 0.0) or 0.0),
                    float(meta.get("delay", 0.0) or 0.0),
                    float(meta.get("width", 0.0) or 0.0),
                ),
            ),
            None,
        )
    if kind == SOURCE_SINE:
        return (
            RustSimSource(
                node=node,
                node_id=node_id,
                kind=SOURCE_SINE,
                params=(
                    scale_f * float(meta.get("offset", 0.0) or 0.0),
                    scale_f * float(meta.get("amplitude", 0.0) or 0.0),
                    float(meta.get("freq", 0.0) or 0.0),
                    float(meta.get("phase", 0.0) or 0.0),
                ),
            ),
            None,
        )
    if kind == SOURCE_PWL:
        times = tuple(float(value) for value in meta.get("times", ()) or ())
        values = tuple(scale_f * float(value) for value in meta.get("values", ()) or ())
        if not times or len(times) != len(values):
            return None, f"source:{node}:invalid_pwl_payload"
        data_start = len(source_data)
        source_data.extend(times)
        source_data.extend(values)
        return (
            RustSimSource(
                node=node,
                node_id=node_id,
                kind=SOURCE_PWL,
                data_start=data_start,
                data_count=len(times),
                params=(),
            ),
            None,
        )
    return None, f"source:{node}:unsupported_waveform:{kind or 'unknown'}"


def build_source_record_rust_program(
    *,
    sources: Iterable[Any],
    current_sources: Iterable[Any] = (),
    recorded_signals: Iterable[str],
    models: Iterable[Any],
    preapplied_initial_step_file_read_model_indices: Iterable[int] = (),
) -> RustSimCompileReport:
    """Lower source+record/no-model simulation into RustSimProgram.

    This is the first strict EVAS2 production slice.  Model semantics are not
    accepted here yet; they must be represented by body/event/transition opcodes
    before a model-containing circuit can run on the Rust-owned loop.
    """

    source_list = tuple(sources)
    current_source_list = tuple(current_sources)
    record_names = tuple(str(name) for name in recorded_signals)
    model_list = _flatten_models_child_first(models)
    preapplied_initial_step_file_read_indices = frozenset(
        int(index) for index in preapplied_initial_step_file_read_model_indices
    )
    global_contributed_nodes = _collect_global_contributed_nodes(model_list)
    reasons: list[str] = []
    if not record_names:
        reasons.append("no_recorded_signals")

    nodes: list[RustSimNode] = []
    states: list[RustSimState] = []
    params: list[RustSimParam] = []
    node_ids: dict[str, int] = {}
    state_ids: dict[tuple[int, str], int] = {}
    param_ids: dict[tuple[int, str], int] = {}
    rust_sources: list[RustSimSource] = []
    continuous_linear_ops: list[RustSimLinearOp] = []
    zi_nd_ops: list[RustSimZiNdOp] = []
    branch_idt_ops: list[RustSimBranchIdtOp] = []
    branch_ddt_ops: list[RustSimBranchDdtOp] = []
    indirect_branch_ode_ops: list[RustSimIndirectBranchOdeOp] = []
    rust_events: list[RustSimEvent] = []
    rust_transitions: list[RustSimTransition] = []
    rust_slews: list[RustSimSlew] = []
    body_stmt_ops: list[BodyStmtOp] = []
    body_expr_ops: list[BodyExprOp] = []
    side_effects: list[RustSimSideEffect] = []
    source_data: list[float] = []
    for source in source_list:
        node = str(getattr(source, "node", ""))
        if not node:
            reasons.append("source_without_node")
            continue
        node_id = _add_node(node, node_ids, nodes)
        meta = _waveform_metadata(getattr(source, "waveform", None))
        if meta is None:
            reasons.append(f"source:{node}:missing_waveform_metadata")
            continue
        rust_source, reason = _source_from_metadata(
            node=node,
            node_id=node_id,
            meta=meta,
            source_data=source_data,
        )
        if reason is not None:
            reasons.append(reason)
        elif rust_source is not None:
            rust_sources.append(rust_source)

    for current_index, item in enumerate(current_source_list):
        try:
            pos, neg, source = item
        except (TypeError, ValueError):
            reasons.append(f"current_source:{current_index}:malformed")
            continue
        pos_name = str(pos)
        neg_name = str(neg)
        meta = _waveform_metadata(getattr(source, "waveform", None))
        if meta is None:
            reasons.append(f"current_source:{pos_name}:{neg_name}:missing_waveform_metadata")
            continue
        for node, scale in (
            (_branch_current_node_name(pos_name, neg_name), -1.0),
            (_branch_current_node_name(neg_name, pos_name), 1.0),
        ):
            node_id = _add_node(node, node_ids, nodes)
            rust_source, reason = _source_from_metadata(
                node=node,
                node_id=node_id,
                meta=meta,
                source_data=source_data,
                scale=scale,
            )
            if reason is not None:
                reasons.append(f"current_{reason}")
            elif rust_source is not None:
                rust_sources.append(rust_source)

    for model_index, model in enumerate(model_list):
        model_cls = getattr(model, "__class__", type(model))
        sampled_zi_nd_raw_ops = tuple(
            getattr(model_cls, "_evaluate_ir_sampled_zi_nd_ops", ()) or ()
        )
        has_branch_current_idt_ops = _model_has_branch_current_idt_ops(model_cls)
        has_branch_current_ddt_ops = _model_has_branch_current_ddt_ops(model_cls)
        has_indirect_branch_ode_ops = _model_has_indirect_branch_ode_ops(model_cls)
        has_continuous_body_candidate = (
            not has_branch_current_idt_ops
            and not has_branch_current_ddt_ops
            and not has_indirect_branch_ode_ops
            and not sampled_zi_nd_raw_ops
            and _model_has_rustsim_continuous_body_candidate(model_cls)
        )
        has_event_transition_ir = bool(
            tuple(getattr(model_cls, "_event_static_linear_ir_ops", ()) or ())
            or tuple(getattr(model_cls, "_event_timer_static_linear_ir_ops", ()) or ())
            or tuple(getattr(model_cls, "_transition_target_ir_ops", ()) or ())
            or tuple(getattr(model_cls, "_whole_segment_candidates", ()) or ())
            or (
                not has_branch_current_idt_ops
                and not has_indirect_branch_ode_ops
                and _model_has_rustsim_event_transition_candidate(model_cls)
            )
            or has_continuous_body_candidate
        )
        has_rustsim_program_ir = bool(
            has_event_transition_ir
            or sampled_zi_nd_raw_ops
            or has_branch_current_idt_ops
            or has_branch_current_ddt_ops
            or has_indirect_branch_ode_ops
        )
        if has_event_transition_ir:
            event_reasons = _convert_event_transition_ops(
                model=model,
                model_index=model_index,
                preapplied_initial_step_file_read=(
                    model_index in preapplied_initial_step_file_read_indices
                ),
                global_contributed_nodes=global_contributed_nodes,
                node_ids=node_ids,
                nodes=nodes,
                state_ids=state_ids,
                states=states,
                param_ids=param_ids,
                params=params,
                events=rust_events,
                transitions=rust_transitions,
                slews=rust_slews,
                body_stmt_ops=body_stmt_ops,
                body_expr_ops=body_expr_ops,
                side_effects=side_effects,
            )
            if event_reasons:
                reasons.extend(event_reasons)
                continue
            if sampled_zi_nd_raw_ops:
                reasons.append(
                    f"model:{model_index}:{model_cls.__name__}:zi_nd_event_transition_mix_not_lowered"
                )
                continue
        elif not has_rustsim_program_ir:
            dynamic_reasons = _reject_model_dynamic_semantics(model, model_index)
            if dynamic_reasons:
                reasons.extend(dynamic_reasons)
                continue
        model_zi_nd_ops, model_zi_reasons = _convert_sampled_zi_nd_ops(
            model=model,
            model_index=model_index,
            node_ids=node_ids,
            nodes=nodes,
            source_data=source_data,
        )
        if model_zi_reasons:
            reasons.extend(model_zi_reasons)
        zi_nd_ops.extend(model_zi_nd_ops)
        model_branch_idt_ops, model_branch_idt_reasons = _convert_branch_current_idt_ops(
            model=model,
            model_index=model_index,
            node_ids=node_ids,
            nodes=nodes,
            state_ids=state_ids,
            states=states,
        )
        if model_branch_idt_reasons:
            reasons.extend(model_branch_idt_reasons)
        branch_idt_ops.extend(model_branch_idt_ops)
        model_branch_ddt_ops, model_branch_ddt_reasons = _convert_branch_current_ddt_ops(
            model=model,
            model_index=model_index,
            node_ids=node_ids,
            nodes=nodes,
            state_ids=state_ids,
            states=states,
        )
        if model_branch_ddt_reasons:
            reasons.extend(model_branch_ddt_reasons)
        branch_ddt_ops.extend(model_branch_ddt_ops)
        model_indirect_ode_ops, model_indirect_ode_reasons = _convert_indirect_branch_ode_ops(
            model=model,
            model_index=model_index,
            node_ids=node_ids,
            nodes=nodes,
            state_ids=state_ids,
            states=states,
        )
        if model_indirect_ode_reasons:
            reasons.extend(model_indirect_ode_reasons)
        indirect_branch_ode_ops.extend(model_indirect_ode_ops)
        model_ops, model_reasons = _convert_continuous_linear_ops(
            model=model,
            model_index=model_index,
            node_ids=node_ids,
            nodes=nodes,
            state_ids=state_ids,
            states=states,
        )
        if has_rustsim_program_ir and all(
            str(reason).endswith(":no_continuous_linear_ir")
            for reason in model_reasons
        ):
            model_reasons = ()
        if (
            getattr(model, "_analog_primitives", None)
            and all(
                str(reason).endswith(":no_continuous_linear_ir")
                for reason in model_reasons
            )
        ):
            model_reasons = ()
        if (
            not has_rustsim_program_ir
            and getattr(model, "_child_models", None)
            and all(
                str(reason).endswith(":no_continuous_linear_ir")
                for reason in model_reasons
            )
        ):
            model_reasons = ()
        if model_reasons:
            reasons.extend(model_reasons)
        continuous_linear_ops.extend(model_ops)

    records: list[RustSimRecord] = []
    for name in record_names:
        node_id = _add_node(name, node_ids, nodes)
        records.append(RustSimRecord(name=name, node_id=node_id))

    if reasons:
        return RustSimCompileReport(
            program=None,
            supported=False,
            reasons=tuple(reasons),
        )

    return RustSimCompileReport(
        program=RustSimProgram(
            nodes=tuple(nodes),
            states=tuple(states),
            params=tuple(params),
            sources=tuple(rust_sources),
            events=tuple(rust_events),
            body_ops=(),
            transitions=tuple(rust_transitions),
            slews=tuple(rust_slews),
            records=tuple(records),
            side_effects=tuple(side_effects),
            continuous_linear_ops=tuple(continuous_linear_ops),
            zi_nd_ops=tuple(zi_nd_ops),
            branch_idt_ops=tuple(branch_idt_ops),
            branch_ddt_ops=tuple(branch_ddt_ops),
            indirect_branch_ode_ops=tuple(indirect_branch_ode_ops),
            body_stmt_ops=tuple(body_stmt_ops),
            body_expr_ops=tuple(body_expr_ops),
            source_data=tuple(source_data),
            bound_step_ops=(),
            final_step_ops=(),
        ),
        supported=True,
        reasons=(),
    )
