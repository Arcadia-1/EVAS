"""General expression IR for Verilog-A body lowering.

This module is the audit-094a bridge between the parser AST and future
statement/event Rust executors.  It intentionally does not change simulator
production behavior.  The first consumer is round-trip validation:

    Verilog-A AST expression -> ExprIR -> Python expression string

The IR keeps source-level structure instead of collapsing everything into the
older static-linear sublanguage in :mod:`evas.simulator.evaluate_ir`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Optional, Tuple, Union

from evas.compiler.ast_nodes import (
    ArrayAccess,
    Assignment,
    BinaryExpr,
    Block,
    BranchAccess,
    CaseStatement,
    CombinedEvent,
    Contribution,
    EventExpr,
    EventStatement,
    Expr,
    ForStatement,
    FunctionCall,
    Identifier,
    IfStatement,
    MethodCall,
    NumberLiteral,
    ParamType,
    StringLiteral,
    SystemTask,
    TernaryExpr,
    UnaryExpr,
    WhileStatement,
)
from evas.simulator.rust_backend import (
    BODY_EXPR_ABS,
    BODY_EXPR_ADD,
    BODY_EXPR_BITAND,
    BODY_EXPR_BITNOT,
    BODY_EXPR_BITOR,
    BODY_EXPR_BITXOR,
    BODY_EXPR_CEIL,
    BODY_EXPR_CONST,
    BODY_EXPR_COS,
    BODY_EXPR_DIV,
    BODY_EXPR_EQ,
    BODY_EXPR_EXP,
    BODY_EXPR_FLOOR,
    BODY_EXPR_GE,
    BODY_EXPR_GT,
    BODY_EXPR_IDIV,
    BODY_EXPR_LAND,
    BODY_EXPR_LE,
    BODY_EXPR_LN,
    BODY_EXPR_LOG10,
    BODY_EXPR_LOR,
    BODY_EXPR_LT,
    BODY_EXPR_MAX,
    BODY_EXPR_MIN,
    BODY_EXPR_MOD,
    BODY_EXPR_MUL,
    BODY_EXPR_NE,
    BODY_EXPR_NEG,
    BODY_EXPR_NOT,
    BODY_EXPR_POW,
    BODY_EXPR_RANDOM_INT32,
    BODY_EXPR_RDIST_ERLANG,
    BODY_EXPR_RDIST_EXPONENTIAL,
    BODY_EXPR_RDIST_NORMAL,
    BODY_EXPR_RDIST_POISSON,
    BODY_EXPR_RDIST_UNIFORM,
    BODY_EXPR_READ_NODE,
    BODY_EXPR_READ_PARAM,
    BODY_EXPR_READ_STATE,
    BODY_EXPR_READ_TIME,
    BODY_EXPR_REDUCE_OR,
    BODY_EXPR_REDUCE_XOR,
    BODY_EXPR_SELECT,
    BODY_EXPR_SHL,
    BODY_EXPR_SHR,
    BODY_EXPR_SIN,
    BODY_EXPR_SQRT,
    BODY_EXPR_SUB,
    BODY_EXPR_TAN,
    BODY_EXPR_TANH,
    BodyExprOp,
)

PURE_MATH_FUNCTIONS = frozenset(
    {
        "abs",
        "ceil",
        "cos",
        "exp",
        "floor",
        "limexp",
        "ln",
        "log",
        "max",
        "min",
        "pow",
        "sin",
        "sqrt",
        "tan",
        "tanh",
    }
)

STATEFUL_ANALOG_FUNCTIONS = frozenset(
    {
        "cross",
        "ddt",
        "idt",
        "idtmod",
        "last_crossing",
        "slew",
        "transition",
    }
)

TRANSIENT_ANALYSIS_FUNCTIONS = frozenset(
    {
        "ac_stim",
        "analysis",
        "flicker_noise",
        "noise_table",
        "white_noise",
    }
)

GENERIC_ACCESS_FUNCTIONS = frozenset({"potential", "flow"})

SUPPORTED_SYSTEM_FUNCTIONS = frozenset(
    {
        "$analog_node_alias",
        "$attribute",
        "$cds_get_mc_trial_number",
        "$dist_uniform",
        "$feof",
        "$fgets",
        "$fopen",
        "$fscanf",
        "$fseek",
        "$ftell",
        "$mfactor",
        "$param_given",
        "$port_connected",
        "$random",
        "$rdist_erlang",
        "$rdist_exponential",
        "$rdist_normal",
        "$rdist_poisson",
        "$rdist_uniform",
        "$rewind",
        "$rtoi",
        "$sformat",
        "$simparam",
        "$table_model",
        "$temperature",
        "$vt",
    }
)

SUPPORTED_METHODS = frozenset({"substr"})

_DOLLAR_MATH_ALIASES = frozenset(f"${name}" for name in PURE_MATH_FUNCTIONS)
SPECIAL_IDENTIFIER_NAMES = frozenset(
    {
        "$abstime",
        "$realtime",
        "$temperature",
        "$vt",
        "inf",
    }
)

SYMBOL_PARAMETER = "parameter"
SYMBOL_PORT = "port"
SYMBOL_SPECIAL = "special"
SYMBOL_STATE_ARRAY = "state_array"
SYMBOL_STATE_SCALAR = "state_scalar"

_BODY_BINARY_OPS = {
    "+": BODY_EXPR_ADD,
    "-": BODY_EXPR_SUB,
    "*": BODY_EXPR_MUL,
    "/": BODY_EXPR_DIV,
    "%": BODY_EXPR_MOD,
    ">": BODY_EXPR_GT,
    "<": BODY_EXPR_LT,
    ">=": BODY_EXPR_GE,
    "<=": BODY_EXPR_LE,
    "==": BODY_EXPR_EQ,
    "!=": BODY_EXPR_NE,
    "&&": BODY_EXPR_LAND,
    "||": BODY_EXPR_LOR,
    "&": BODY_EXPR_BITAND,
    "|": BODY_EXPR_BITOR,
    "^": BODY_EXPR_BITXOR,
    "<<": BODY_EXPR_SHL,
    ">>": BODY_EXPR_SHR,
}

_BODY_UNARY_OPS = {
    "-": BODY_EXPR_NEG,
    "!": BODY_EXPR_NOT,
    "~": BODY_EXPR_BITNOT,
    "^": BODY_EXPR_REDUCE_XOR,
    "|": BODY_EXPR_REDUCE_OR,
}

_BODY_FUNCTION_OPS = {
    "abs": (BODY_EXPR_ABS, 1),
    "sqrt": (BODY_EXPR_SQRT, 1),
    "exp": (BODY_EXPR_EXP, 1),
    "limexp": (BODY_EXPR_EXP, 1),
    "ln": (BODY_EXPR_LN, 1),
    "log": (BODY_EXPR_LOG10, 1),
    "sin": (BODY_EXPR_SIN, 1),
    "cos": (BODY_EXPR_COS, 1),
    "tan": (BODY_EXPR_TAN, 1),
    "tanh": (BODY_EXPR_TANH, 1),
    "floor": (BODY_EXPR_FLOOR, 1),
    "ceil": (BODY_EXPR_CEIL, 1),
    "min": (BODY_EXPR_MIN, 2),
    "max": (BODY_EXPR_MAX, 2),
    "pow": (BODY_EXPR_POW, 2),
    "$random": (BODY_EXPR_RANDOM_INT32, 1),
    "$rdist_exponential": (BODY_EXPR_RDIST_EXPONENTIAL, 2),
    "$rdist_poisson": (BODY_EXPR_RDIST_POISSON, 2),
    "$rdist_normal": (BODY_EXPR_RDIST_NORMAL, 3),
    "$rdist_erlang": (BODY_EXPR_RDIST_ERLANG, 3),
    "$dist_uniform": (BODY_EXPR_RDIST_UNIFORM, 3),
    "$rdist_uniform": (BODY_EXPR_RDIST_UNIFORM, 3),
}

_DEFAULT_TEMPERATURE_C = 27.0
_DEFAULT_TEMPERATURE_K = _DEFAULT_TEMPERATURE_C + 273.15
_BOLTZMANN_OVER_Q = 1.380649e-23 / 1.602176634e-19


@dataclass(frozen=True)
class LoweringContext:
    """Policy for expression lowering.

    The default context only admits pure math functions.  Broader contexts are
    used for body round-trip validation, where the IR must preserve expressions
    that later statement/event lowering will decide whether Rust can execute.
    """

    allowed_functions: frozenset[str] = PURE_MATH_FUNCTIONS
    allowed_system_functions: frozenset[str] = frozenset()
    allowed_methods: frozenset[str] = frozenset()
    allowed_branch_access_types: frozenset[str] = frozenset({"V", "I"})

    @classmethod
    def pure_math(cls) -> "LoweringContext":
        return cls()

    @classmethod
    def veriloga_body(cls) -> "LoweringContext":
        return cls(
            allowed_functions=(
                PURE_MATH_FUNCTIONS
                | STATEFUL_ANALOG_FUNCTIONS
                | TRANSIENT_ANALYSIS_FUNCTIONS
                | GENERIC_ACCESS_FUNCTIONS
            ),
            allowed_system_functions=SUPPORTED_SYSTEM_FUNCTIONS,
            allowed_methods=SUPPORTED_METHODS,
        )


@dataclass(frozen=True)
class LiteralIR:
    value: object
    raw: Optional[str] = None


@dataclass(frozen=True)
class IdentifierIR:
    name: str


@dataclass(frozen=True)
class ArrayAccessIR:
    name: str
    index: "ExprIR"


@dataclass(frozen=True)
class BinaryExprIR:
    op: str
    left: "ExprIR"
    right: "ExprIR"


@dataclass(frozen=True)
class UnaryExprIR:
    op: str
    operand: "ExprIR"


@dataclass(frozen=True)
class TernaryExprIR:
    cond: "ExprIR"
    true_expr: "ExprIR"
    false_expr: "ExprIR"


@dataclass(frozen=True)
class FunctionCallIR:
    name: str
    args: Tuple["ExprIR", ...]


@dataclass(frozen=True)
class BranchAccessIR:
    access_type: str
    node1: str
    node2: Optional[str] = None
    node1_index: Optional["ExprIR"] = None
    node2_index: Optional["ExprIR"] = None
    node1_index2: Optional["ExprIR"] = None
    node2_index2: Optional["ExprIR"] = None


@dataclass(frozen=True)
class MethodCallIR:
    obj: str
    method: str
    args: Tuple["ExprIR", ...]


@dataclass(frozen=True)
class StateBindingIR:
    """One source-level symbol bound to a stable typed-array slot."""

    name: str
    kind: str
    slot: int
    integer: bool = False
    lo: Optional[int] = None
    hi: Optional[int] = None


@dataclass(frozen=True)
class BindingTableIR:
    """Bindings for state, parameter, port, and special identifiers."""

    bindings: Tuple[StateBindingIR, ...]

    def resolve(self, name: str) -> Optional[StateBindingIR]:
        for binding in self.bindings:
            if binding.name == name:
                return binding
        return None


ExprIR = Union[
    LiteralIR,
    IdentifierIR,
    ArrayAccessIR,
    BinaryExprIR,
    UnaryExprIR,
    TernaryExprIR,
    FunctionCallIR,
    BranchAccessIR,
    MethodCallIR,
]


def lower_expr(
    ast_expr: Expr,
    context: Optional[LoweringContext] = None,
) -> Optional[ExprIR]:
    """Lower a parser AST expression into ExprIR.

    Returns ``None`` for constructs outside the supplied policy.  This is a
    deliberate gate: later production lowerings can widen the context only when
    their runtime semantics are ready.
    """

    ctx = context or LoweringContext.pure_math()

    if isinstance(ast_expr, NumberLiteral):
        return LiteralIR(float(ast_expr.value), getattr(ast_expr, "raw", None))

    if isinstance(ast_expr, StringLiteral):
        return LiteralIR(str(ast_expr.value), None)

    if isinstance(ast_expr, Identifier):
        return IdentifierIR(str(ast_expr.name))

    if isinstance(ast_expr, ArrayAccess):
        index = lower_expr(ast_expr.index, ctx)
        if index is None:
            return None
        return ArrayAccessIR(str(ast_expr.name), index)

    if isinstance(ast_expr, BinaryExpr):
        left = lower_expr(ast_expr.left, ctx)
        right = lower_expr(ast_expr.right, ctx)
        if left is None or right is None:
            return None
        return BinaryExprIR(str(ast_expr.op), left, right)

    if isinstance(ast_expr, UnaryExpr):
        operand = lower_expr(ast_expr.operand, ctx)
        if operand is None:
            return None
        return UnaryExprIR(str(ast_expr.op), operand)

    if isinstance(ast_expr, TernaryExpr):
        cond = lower_expr(ast_expr.cond, ctx)
        true_expr = lower_expr(ast_expr.true_expr, ctx)
        false_expr = lower_expr(ast_expr.false_expr, ctx)
        if cond is None or true_expr is None or false_expr is None:
            return None
        return TernaryExprIR(cond, true_expr, false_expr)

    if isinstance(ast_expr, FunctionCall):
        name = _normalize_function_name(str(ast_expr.name))
        if name not in ctx.allowed_functions:
            if str(ast_expr.name) not in ctx.allowed_system_functions:
                return None
            name = str(ast_expr.name)
        args = _lower_expr_tuple(ast_expr.args, ctx)
        if args is None:
            return None
        if name in {"ac_stim", "flicker_noise", "noise_table", "white_noise"}:
            return LiteralIR(0.0)
        if name == "analysis":
            if args and isinstance(args[0], LiteralIR):
                analysis_name = str(args[0].value).strip().lower()
                return LiteralIR(1.0 if analysis_name in {"tran", "transient"} else 0.0)
            return LiteralIR(0.0)
        return FunctionCallIR(name, args)

    if isinstance(ast_expr, BranchAccess):
        access_type = str(ast_expr.access_type)
        if access_type not in ctx.allowed_branch_access_types:
            return None
        node1_index = _lower_optional_expr(ast_expr.node1_index, ctx)
        node1_index2 = _lower_optional_expr(ast_expr.node1_index2, ctx)
        node2_index = _lower_optional_expr(ast_expr.node2_index, ctx)
        node2_index2 = _lower_optional_expr(ast_expr.node2_index2, ctx)
        if _any_missing(node1_index, ast_expr.node1_index):
            return None
        if _any_missing(node1_index2, ast_expr.node1_index2):
            return None
        if _any_missing(node2_index, ast_expr.node2_index):
            return None
        if _any_missing(node2_index2, ast_expr.node2_index2):
            return None
        return BranchAccessIR(
            access_type=access_type,
            node1=str(ast_expr.node1),
            node2=str(ast_expr.node2) if ast_expr.node2 is not None else None,
            node1_index=node1_index,
            node2_index=node2_index,
            node1_index2=node1_index2,
            node2_index2=node2_index2,
        )

    if isinstance(ast_expr, MethodCall):
        method = str(ast_expr.method)
        if method not in ctx.allowed_methods:
            return None
        args = _lower_expr_tuple(ast_expr.args, ctx)
        if args is None:
            return None
        return MethodCallIR(str(ast_expr.obj), method, args)

    return None


def emit_python(expr_ir: ExprIR) -> str:
    """Emit a valid Python expression for round-trip validation."""

    if isinstance(expr_ir, LiteralIR):
        if isinstance(expr_ir.value, float):
            if expr_ir.value == float("inf"):
                return "float('inf')"
            if expr_ir.value == float("-inf"):
                return "float('-inf')"
            if expr_ir.value != expr_ir.value:
                return "float('nan')"
        return repr(expr_ir.value)

    if isinstance(expr_ir, IdentifierIR):
        return _emit_identifier(expr_ir.name)

    if isinstance(expr_ir, ArrayAccessIR):
        return f"array_value({expr_ir.name!r}, int({emit_python(expr_ir.index)}))"

    if isinstance(expr_ir, BinaryExprIR):
        left = emit_python(expr_ir.left)
        right = emit_python(expr_ir.right)
        op = expr_ir.op
        if op == "&&":
            return f"(({left}) and ({right}))"
        if op == "||":
            return f"(({left}) or ({right}))"
        if op == "^":
            return f"(int({left}) ^ int({right}))"
        if op == "&":
            return f"(int({left}) & int({right}))"
        if op == "|":
            return f"(int({left}) | int({right}))"
        if op == "<<":
            return f"(int({left}) << int({right}))"
        if op == ">>":
            return f"(int({left}) >> int({right}))"
        return f"(({left}) {op} ({right}))"

    if isinstance(expr_ir, UnaryExprIR):
        operand = emit_python(expr_ir.operand)
        if expr_ir.op == "!":
            return f"(not ({operand}))"
        if expr_ir.op == "~":
            return f"(~int({operand}))"
        if expr_ir.op == "^":
            return f"(int({operand}).bit_count() & 1)"
        if expr_ir.op == "|":
            return f"(1 if int({operand}) != 0 else 0)"
        return f"({expr_ir.op}({operand}))"

    if isinstance(expr_ir, TernaryExprIR):
        cond = emit_python(expr_ir.cond)
        true_expr = emit_python(expr_ir.true_expr)
        false_expr = emit_python(expr_ir.false_expr)
        return f"(({true_expr}) if ({cond}) else ({false_expr}))"

    if isinstance(expr_ir, FunctionCallIR):
        return _emit_function_call(expr_ir)

    if isinstance(expr_ir, BranchAccessIR):
        return _emit_branch_access(expr_ir)

    if isinstance(expr_ir, MethodCallIR):
        args = ", ".join(emit_python(arg) for arg in expr_ir.args)
        if expr_ir.method == "substr":
            return f"method_substr({expr_ir.obj!r}, {args})"
        return f"method_call({expr_ir.obj!r}, {expr_ir.method!r}, {args})"

    raise TypeError(f"unsupported ExprIR node: {expr_ir!r}")


def iter_exprs_from_statement(stmt: object) -> Iterator[Expr]:
    """Yield expressions contained in a statement/event tree."""

    if stmt is None:
        return

    if isinstance(stmt, Block):
        for child in stmt.statements:
            yield from iter_exprs_from_statement(child)
        return

    if isinstance(stmt, Assignment):
        yield stmt.target
        yield stmt.value
        return

    if isinstance(stmt, Contribution):
        yield stmt.branch
        yield stmt.expr
        return

    if isinstance(stmt, EventStatement):
        yield from iter_exprs_from_event(stmt.event)
        yield from iter_exprs_from_statement(stmt.body)
        return

    if isinstance(stmt, IfStatement):
        yield stmt.cond
        yield from iter_exprs_from_statement(stmt.then_body)
        yield from iter_exprs_from_statement(stmt.else_body)
        return

    if isinstance(stmt, ForStatement):
        yield from iter_exprs_from_statement(stmt.init)
        yield stmt.cond
        yield from iter_exprs_from_statement(stmt.update)
        yield from iter_exprs_from_statement(stmt.body)
        return

    if isinstance(stmt, WhileStatement):
        yield stmt.cond
        yield from iter_exprs_from_statement(stmt.body)
        return

    if isinstance(stmt, CaseStatement):
        yield stmt.expr
        for item in stmt.items:
            for value in item.values:
                yield value
            yield from iter_exprs_from_statement(item.body)
        return

    if isinstance(stmt, SystemTask):
        for arg in stmt.args:
            yield arg


def iter_exprs_from_event(event: object) -> Iterator[Expr]:
    if isinstance(event, CombinedEvent):
        for child in event.events:
            yield from iter_exprs_from_event(child)
        return

    if isinstance(event, EventExpr):
        for arg in event.args:
            yield arg
        if event.time_tol_expr is not None:
            yield event.time_tol_expr
        if event.expr_tol_expr is not None:
            yield event.expr_tol_expr


def build_state_binding_ir(module: object) -> BindingTableIR:
    """Build stable symbol bindings from a parsed module.

    The binding is representation-only.  Later Rust ABI work can choose which
    binding kinds become arrays and which remain Python fallback metadata.
    """

    bindings: list[StateBindingIR] = []

    for slot, name in enumerate(sorted(SPECIAL_IDENTIFIER_NAMES)):
        bindings.append(StateBindingIR(name=name, kind=SYMBOL_SPECIAL, slot=slot))

    for slot, param in enumerate(getattr(module, "parameters", ()) or ()):
        integer = getattr(param, "param_type", None) == ParamType.INTEGER
        is_string = getattr(param, "param_type", None) == ParamType.STRING
        if is_string:
            continue
        bindings.append(
            StateBindingIR(
                name=str(param.name),
                kind=SYMBOL_PARAMETER,
                slot=slot,
                integer=integer,
            )
        )

    for slot, name in enumerate(getattr(module, "ports", ()) or ()):
        bindings.append(StateBindingIR(name=str(name), kind=SYMBOL_PORT, slot=slot))

    variables = tuple(getattr(module, "variables", ()) or ())
    scalar_slot = 0
    for variable in variables:
        integer = getattr(variable, "var_type", None) == ParamType.INTEGER
        is_string = getattr(variable, "var_type", None) == ParamType.STRING
        if getattr(variable, "is_array", False):
            continue
        if is_string:
            continue
        bindings.append(
            StateBindingIR(
                name=str(variable.name),
                kind=SYMBOL_STATE_SCALAR,
                slot=scalar_slot,
                integer=integer,
            )
        )
        scalar_slot += 1

    array_slot = 0
    for variable in variables:
        integer = getattr(variable, "var_type", None) == ParamType.INTEGER
        is_string = getattr(variable, "var_type", None) == ParamType.STRING
        if is_string:
            continue
        if getattr(variable, "is_array", False):
            raw_lo = getattr(variable, "array_lo", None)
            raw_hi = getattr(variable, "array_hi", None)
            lo = int(raw_lo if raw_lo is not None else 0)
            hi = int(raw_hi if raw_hi is not None else 0)
            lo_i = min(lo, hi)
            hi_i = max(lo, hi)
            bindings.append(
                StateBindingIR(
                    name=str(variable.name),
                    kind=SYMBOL_STATE_ARRAY,
                    slot=array_slot,
                    integer=integer,
                    lo=lo_i,
                    hi=hi_i,
                )
            )
            for idx in range(lo_i, hi_i + 1):
                bindings.append(
                    StateBindingIR(
                        name=_state_array_slot_name(str(variable.name), idx),
                        kind=SYMBOL_STATE_SCALAR,
                        slot=scalar_slot,
                        integer=integer,
                        lo=idx,
                        hi=idx,
                    )
                )
                scalar_slot += 1
            array_slot += 1

    return BindingTableIR(tuple(bindings))


def resolve_static_array_element_binding(
    expr_ir: ArrayAccessIR,
    bindings: BindingTableIR,
) -> Optional[StateBindingIR]:
    """Resolve ``arr[constant]`` to the flattened scalar state slot binding."""

    array_binding = bindings.resolve(expr_ir.name)
    if array_binding is None or array_binding.kind != SYMBOL_STATE_ARRAY:
        return None
    idx = _static_integer_expr_value(expr_ir.index)
    if idx is None:
        return None
    if array_binding.lo is not None and idx < int(array_binding.lo):
        return None
    if array_binding.hi is not None and idx > int(array_binding.hi):
        return None
    binding = bindings.resolve(_state_array_slot_name(expr_ir.name, idx))
    if binding is None or binding.kind != SYMBOL_STATE_SCALAR:
        return None
    return binding


def static_array_element_name(
    expr_ir: ArrayAccessIR,
    bindings: BindingTableIR,
) -> Optional[str]:
    binding = resolve_static_array_element_binding(expr_ir, bindings)
    return binding.name if binding is not None else None


def static_node_ref_name(
    name: str,
    index1: Optional[ExprIR] = None,
    index2: Optional[ExprIR] = None,
) -> Optional[str]:
    """Return a compile-time node name for ``node[i]`` / ``node[i][j]``.

    Dynamic index expressions return ``None`` so production Rust lowerings can
    keep falling back instead of guessing a runtime node-id mapping.
    """

    if index1 is None:
        return str(name)
    idx1 = _static_integer_expr_value(index1)
    if idx1 is None:
        return None
    if index2 is None:
        return f"{name}[{idx1}]"
    idx2 = _static_integer_expr_value(index2)
    if idx2 is None:
        return None
    return f"{name}[{idx1}][{idx2}]"


def iter_identifier_names(expr_ir: ExprIR) -> Iterator[str]:
    """Yield source identifier names referenced by an ExprIR tree."""

    if isinstance(expr_ir, IdentifierIR):
        yield expr_ir.name
        return

    if isinstance(expr_ir, ArrayAccessIR):
        yield expr_ir.name
        yield from iter_identifier_names(expr_ir.index)
        return

    if isinstance(expr_ir, BinaryExprIR):
        yield from iter_identifier_names(expr_ir.left)
        yield from iter_identifier_names(expr_ir.right)
        return

    if isinstance(expr_ir, UnaryExprIR):
        yield from iter_identifier_names(expr_ir.operand)
        return

    if isinstance(expr_ir, TernaryExprIR):
        yield from iter_identifier_names(expr_ir.cond)
        yield from iter_identifier_names(expr_ir.true_expr)
        yield from iter_identifier_names(expr_ir.false_expr)
        return

    if isinstance(expr_ir, FunctionCallIR):
        for arg in expr_ir.args:
            yield from iter_identifier_names(arg)
        return

    if isinstance(expr_ir, BranchAccessIR):
        for child in (
            expr_ir.node1_index,
            expr_ir.node1_index2,
            expr_ir.node2_index,
            expr_ir.node2_index2,
        ):
            if child is not None:
                yield from iter_identifier_names(child)
        return

    if isinstance(expr_ir, MethodCallIR):
        yield expr_ir.obj
        for arg in expr_ir.args:
            yield from iter_identifier_names(arg)


def encode_body_expr_ops(
    expr_ir: ExprIR,
    bindings: BindingTableIR,
    node_slots: Mapping[str, int],
) -> Optional[Tuple[BodyExprOp, ...]]:
    """Encode ExprIR into the 094e Rust body stack-machine op stream.

    The encoder is intentionally conservative.  It only accepts scalar
    parameters, scalar states, and statically resolved voltage node reads.  More
    complex language features still return ``None`` so production callers can
    fall back to the Python evaluator without changing semantics.
    """

    ops: list[BodyExprOp] = []
    if not _append_body_expr_ops(expr_ir, bindings, node_slots, ops):
        return None
    return tuple(ops)


def _append_body_expr_ops(
    expr_ir: ExprIR,
    bindings: BindingTableIR,
    node_slots: Mapping[str, int],
    ops: list[BodyExprOp],
) -> bool:
    if isinstance(expr_ir, LiteralIR):
        if not isinstance(expr_ir.value, (int, float)):
            return False
        ops.append(BodyExprOp(BODY_EXPR_CONST, value=float(expr_ir.value)))
        return True

    if isinstance(expr_ir, IdentifierIR):
        if expr_ir.name in {"$abstime", "$realtime"}:
            ops.append(BodyExprOp(BODY_EXPR_READ_TIME))
            return True
        if expr_ir.name == "$temperature":
            ops.append(BodyExprOp(BODY_EXPR_CONST, value=_DEFAULT_TEMPERATURE_K))
            return True
        if expr_ir.name == "$vt":
            ops.append(
                BodyExprOp(
                    BODY_EXPR_CONST,
                    value=_BOLTZMANN_OVER_Q * _DEFAULT_TEMPERATURE_K,
                )
            )
            return True
        if expr_ir.name == "inf":
            ops.append(BodyExprOp(BODY_EXPR_CONST, value=float("inf")))
            return True
        binding = bindings.resolve(expr_ir.name)
        if binding is None:
            return False
        if binding.kind == SYMBOL_PARAMETER:
            ops.append(BodyExprOp(BODY_EXPR_READ_PARAM, index=binding.slot))
            return True
        if binding.kind == SYMBOL_STATE_SCALAR:
            ops.append(BodyExprOp(BODY_EXPR_READ_STATE, index=binding.slot))
            return True
        if binding.kind == SYMBOL_PORT and expr_ir.name in node_slots:
            ops.append(BodyExprOp(BODY_EXPR_READ_NODE, index=node_slots[expr_ir.name]))
            return True
        return False

    if isinstance(expr_ir, ArrayAccessIR):
        binding = resolve_static_array_element_binding(expr_ir, bindings)
        if binding is None:
            return False
        ops.append(BodyExprOp(BODY_EXPR_READ_STATE, index=binding.slot))
        return True

    if isinstance(expr_ir, BranchAccessIR):
        return _append_branch_body_expr_ops(expr_ir, bindings, node_slots, ops)

    if isinstance(expr_ir, BinaryExprIR):
        op_kind = _BODY_BINARY_OPS.get(expr_ir.op)
        if op_kind is None:
            return False
        if expr_ir.op == "/" and _expr_ir_is_integer(expr_ir, bindings):
            op_kind = BODY_EXPR_IDIV
        if not _append_body_expr_ops(expr_ir.left, bindings, node_slots, ops):
            return False
        if not _append_body_expr_ops(expr_ir.right, bindings, node_slots, ops):
            return False
        ops.append(BodyExprOp(op_kind))
        return True

    if isinstance(expr_ir, UnaryExprIR):
        if expr_ir.op == "+":
            return _append_body_expr_ops(expr_ir.operand, bindings, node_slots, ops)
        op_kind = _BODY_UNARY_OPS.get(expr_ir.op)
        if op_kind is None:
            return False
        if not _append_body_expr_ops(expr_ir.operand, bindings, node_slots, ops):
            return False
        ops.append(BodyExprOp(op_kind))
        return True

    if isinstance(expr_ir, TernaryExprIR):
        if not _append_body_expr_ops(expr_ir.cond, bindings, node_slots, ops):
            return False
        if not _append_body_expr_ops(expr_ir.true_expr, bindings, node_slots, ops):
            return False
        if not _append_body_expr_ops(expr_ir.false_expr, bindings, node_slots, ops):
            return False
        ops.append(BodyExprOp(BODY_EXPR_SELECT))
        return True

    if isinstance(expr_ir, FunctionCallIR):
        if expr_ir.name in {"potential", "flow"}:
            branch = _generic_access_function_to_branch(expr_ir)
            if branch is None:
                return False
            return _append_branch_body_expr_ops(branch, bindings, node_slots, ops)
        if expr_ir.name == "$attribute":
            return _append_attribute_body_expr_ops(expr_ir, ops)
        if expr_ir.name == "$temperature":
            if expr_ir.args:
                return False
            ops.append(BodyExprOp(BODY_EXPR_CONST, value=_DEFAULT_TEMPERATURE_K))
            return True
        if expr_ir.name == "$vt":
            if len(expr_ir.args) > 1:
                return False
            if not expr_ir.args:
                ops.append(
                    BodyExprOp(
                        BODY_EXPR_CONST,
                        value=_BOLTZMANN_OVER_Q * _DEFAULT_TEMPERATURE_K,
                    )
                )
                return True
            if not _append_body_expr_ops(expr_ir.args[0], bindings, node_slots, ops):
                return False
            ops.append(BodyExprOp(BODY_EXPR_CONST, value=_BOLTZMANN_OVER_Q))
            ops.append(BodyExprOp(BODY_EXPR_MUL))
            return True
        if expr_ir.name == "$simparam":
            encoded = _append_simparam_body_expr_ops(
                expr_ir,
                bindings,
                node_slots,
                ops,
            )
            if encoded is not None:
                return encoded
        if expr_ir.name == "$cds_get_mc_trial_number":
            if expr_ir.args:
                return False
            ops.append(BodyExprOp(BODY_EXPR_CONST, value=0.0))
            return True
        if expr_ir.name == "$rtoi":
            if len(expr_ir.args) != 1:
                return False
            if not _append_rtoi_body_expr_ops(
                expr_ir.args[0],
                bindings,
                node_slots,
                ops,
            ):
                return False
            return True
        op_info = _BODY_FUNCTION_OPS.get(expr_ir.name)
        if op_info is None:
            return False
        op_kind, arity = op_info
        if len(expr_ir.args) != arity:
            return False
        for arg in expr_ir.args:
            if not _append_body_expr_ops(arg, bindings, node_slots, ops):
                return False
        ops.append(BodyExprOp(op_kind))
        return True

    return False


def _append_rtoi_body_expr_ops(
    arg: ExprIR,
    bindings: BindingTableIR,
    node_slots: Mapping[str, int],
    ops: list[BodyExprOp],
) -> bool:
    if isinstance(arg, LiteralIR) and isinstance(arg.value, (int, float)):
        value = float(arg.value)
        rounded = math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5)
        ops.append(BodyExprOp(BODY_EXPR_CONST, value=float(rounded)))
        return True

    if not _append_body_expr_ops(arg, bindings, node_slots, ops):
        return False
    ops.append(BodyExprOp(BODY_EXPR_CONST, value=0.0))
    ops.append(BodyExprOp(BODY_EXPR_GE))

    if not _append_body_expr_ops(arg, bindings, node_slots, ops):
        return False
    ops.append(BodyExprOp(BODY_EXPR_CONST, value=0.5))
    ops.append(BodyExprOp(BODY_EXPR_ADD))
    ops.append(BodyExprOp(BODY_EXPR_FLOOR))

    if not _append_body_expr_ops(arg, bindings, node_slots, ops):
        return False
    ops.append(BodyExprOp(BODY_EXPR_CONST, value=0.5))
    ops.append(BodyExprOp(BODY_EXPR_SUB))
    ops.append(BodyExprOp(BODY_EXPR_CEIL))

    ops.append(BodyExprOp(BODY_EXPR_SELECT))
    return True


def _generic_access_function_to_branch(expr_ir: FunctionCallIR) -> Optional[BranchAccessIR]:
    if expr_ir.name not in {"potential", "flow"} or not expr_ir.args:
        return None

    first = _node_ref_from_expr_ir(expr_ir.args[0])
    if first is None:
        return None
    node1, node1_index, node1_index2 = first

    node2 = node2_index = node2_index2 = None
    if len(expr_ir.args) > 1:
        second = _node_ref_from_expr_ir(expr_ir.args[1])
        if second is None:
            return None
        node2, node2_index, node2_index2 = second

    return BranchAccessIR(
        access_type="V" if expr_ir.name == "potential" else "I",
        node1=node1,
        node2=node2,
        node1_index=node1_index,
        node2_index=node2_index,
        node1_index2=node1_index2,
        node2_index2=node2_index2,
    )


def _node_ref_from_expr_ir(
    expr_ir: ExprIR,
) -> Optional[tuple[str, Optional[ExprIR], Optional[ExprIR]]]:
    if isinstance(expr_ir, IdentifierIR):
        return expr_ir.name, None, None
    if isinstance(expr_ir, ArrayAccessIR):
        return expr_ir.name, expr_ir.index, None
    return None


def _append_attribute_body_expr_ops(
    expr_ir: FunctionCallIR,
    ops: list[BodyExprOp],
) -> bool:
    if len(expr_ir.args) != 1:
        return False
    key_expr = expr_ir.args[0]
    if not isinstance(key_expr, LiteralIR) or not isinstance(key_expr.value, str):
        return False
    key = str(key_expr.value).strip().lower()
    if key.endswith(".potential.abstol"):
        value = 1.0e-6
    elif key.endswith(".flow.abstol"):
        value = 1.0e-12
    elif key.endswith(".abstol"):
        value = 1.0e-12
    else:
        value = 0.0
    ops.append(BodyExprOp(BODY_EXPR_CONST, value=value))
    return True


def _append_simparam_body_expr_ops(
    expr_ir: FunctionCallIR,
    bindings: BindingTableIR,
    node_slots: Mapping[str, int],
    ops: list[BodyExprOp],
) -> Optional[bool]:
    if not expr_ir.args or len(expr_ir.args) > 2:
        return False
    key_expr = expr_ir.args[0]
    if not isinstance(key_expr, LiteralIR) or not isinstance(key_expr.value, str):
        return False
    key = str(key_expr.value).strip().lower()
    values = {
        "temp": _DEFAULT_TEMPERATURE_C,
        "temperature": _DEFAULT_TEMPERATURE_C,
        "tnom": _DEFAULT_TEMPERATURE_C,
        "gmin": 1e-12,
        "reltol": 1e-3,
        "abstol": 1e-12,
        "iabstol": 1e-12,
        "vabstol": 1e-6,
    }
    if key in values:
        ops.append(BodyExprOp(BODY_EXPR_CONST, value=values[key]))
        return True
    if len(expr_ir.args) == 2:
        return _append_body_expr_ops(expr_ir.args[1], bindings, node_slots, ops)
    ops.append(BodyExprOp(BODY_EXPR_CONST, value=0.0))
    return True


def _expr_ir_is_integer(expr_ir: ExprIR, bindings: BindingTableIR) -> bool:
    integer_like, has_typed_integer = _expr_ir_integer_kind(expr_ir, bindings)
    return integer_like and has_typed_integer


def _expr_ir_integer_kind(expr_ir: ExprIR, bindings: BindingTableIR) -> tuple[bool, bool]:
    if isinstance(expr_ir, LiteralIR):
        raw = expr_ir.raw
        if raw:
            token = raw.lstrip("+-")
            is_plain_integer = (
                token.isdigit()
                and "." not in token
                and "e" not in token.lower()
            )
            return is_plain_integer, False
        if isinstance(expr_ir.value, (int, float)):
            try:
                return float(expr_ir.value).is_integer(), False
            except (TypeError, ValueError):
                return False, False
        return False, False

    if isinstance(expr_ir, IdentifierIR):
        binding = bindings.resolve(expr_ir.name)
        if binding is None:
            return False, False
        if binding.kind in {SYMBOL_PARAMETER, SYMBOL_STATE_SCALAR} and binding.integer:
            return True, True
        return False, False

    if isinstance(expr_ir, ArrayAccessIR):
        binding = resolve_static_array_element_binding(expr_ir, bindings)
        if binding is None:
            return False, False
        return binding.integer, binding.integer

    if isinstance(expr_ir, BinaryExprIR):
        if expr_ir.op in {"%", "<<", ">>", "&", "|", "^"}:
            return True, True
        if expr_ir.op in {"+", "-", "*", "/"}:
            left_like, left_typed = _expr_ir_integer_kind(expr_ir.left, bindings)
            right_like, right_typed = _expr_ir_integer_kind(expr_ir.right, bindings)
            return left_like and right_like, left_typed or right_typed
        return False, False

    if isinstance(expr_ir, UnaryExprIR):
        if expr_ir.op in {"&", "|", "^", "~"}:
            return True, True
        return _expr_ir_integer_kind(expr_ir.operand, bindings)

    if isinstance(expr_ir, TernaryExprIR):
        true_like, true_typed = _expr_ir_integer_kind(expr_ir.true_expr, bindings)
        false_like, false_typed = _expr_ir_integer_kind(expr_ir.false_expr, bindings)
        return true_like and false_like, true_typed or false_typed

    return False, False


def _state_array_slot_name(name: str, idx: int) -> str:
    return f"{name}[{int(idx)}]"


def _static_integer_expr_value(expr_ir: ExprIR) -> Optional[int]:
    numeric = _static_numeric_expr_value(expr_ir)
    if numeric is None:
        return None
    idx = int(numeric)
    if numeric != float(idx):
        return None
    return idx


def _static_numeric_expr_value(expr_ir: ExprIR) -> Optional[float]:
    if isinstance(expr_ir, LiteralIR):
        value = expr_ir.value
        if not isinstance(value, (int, float)):
            return None
        return float(value)
    if isinstance(expr_ir, UnaryExprIR):
        value = _static_numeric_expr_value(expr_ir.operand)
        if value is None:
            return None
        if expr_ir.op == "+":
            return value
        if expr_ir.op == "-":
            return -value
        if expr_ir.op == "!":
            return 0.0 if value else 1.0
        if expr_ir.op == "~":
            return float(~int(value))
        return None
    if isinstance(expr_ir, BinaryExprIR):
        left = _static_numeric_expr_value(expr_ir.left)
        right = _static_numeric_expr_value(expr_ir.right)
        if left is None or right is None:
            return None
        try:
            if expr_ir.op == "+":
                return left + right
            if expr_ir.op == "-":
                return left - right
            if expr_ir.op == "*":
                return left * right
            if expr_ir.op == "/":
                return None if right == 0.0 else left / right
            if expr_ir.op == "%":
                return None if right == 0.0 else float(int(left) % int(right))
            if expr_ir.op == "<<":
                return float(int(left) << int(right))
            if expr_ir.op == ">>":
                return float(int(left) >> int(right))
            if expr_ir.op == "&":
                return float(int(left) & int(right))
            if expr_ir.op == "|":
                return float(int(left) | int(right))
            if expr_ir.op == "^":
                return float(int(left) ^ int(right))
            if expr_ir.op == ">":
                return 1.0 if left > right else 0.0
            if expr_ir.op == "<":
                return 1.0 if left < right else 0.0
            if expr_ir.op == ">=":
                return 1.0 if left >= right else 0.0
            if expr_ir.op == "<=":
                return 1.0 if left <= right else 0.0
            if expr_ir.op == "==":
                return 1.0 if left == right else 0.0
            if expr_ir.op == "!=":
                return 1.0 if left != right else 0.0
            if expr_ir.op == "&&":
                return 1.0 if left and right else 0.0
            if expr_ir.op == "||":
                return 1.0 if left or right else 0.0
        except (OverflowError, ValueError):
            return None
        return None
    if isinstance(expr_ir, TernaryExprIR):
        cond = _static_numeric_expr_value(expr_ir.cond)
        if cond is None:
            return None
        return _static_numeric_expr_value(
            expr_ir.true_expr if cond else expr_ir.false_expr
        )
    return None


def _append_branch_body_expr_ops(
    expr_ir: BranchAccessIR,
    bindings: BindingTableIR,
    node_slots: Mapping[str, int],
    ops: list[BodyExprOp],
) -> bool:
    node1_name = static_node_ref_name(
        expr_ir.node1,
        expr_ir.node1_index,
        expr_ir.node1_index2,
    )
    if node1_name is None:
        return False
    if expr_ir.access_type == "I":
        if expr_ir.node2 is None:
            return False
        node2_name = static_node_ref_name(
            expr_ir.node2,
            expr_ir.node2_index,
            expr_ir.node2_index2,
        )
        if node2_name is None:
            return False
        current_slot = node_slots.get(_branch_current_node_name(node1_name, node2_name))
        if current_slot is None:
            return False
        ops.append(BodyExprOp(BODY_EXPR_READ_NODE, index=current_slot))
        return True
    if expr_ir.access_type != "V":
        return False
    node1_slot = node_slots.get(node1_name)
    if node1_slot is None:
        return False
    ops.append(BodyExprOp(BODY_EXPR_READ_NODE, index=node1_slot))
    if expr_ir.node2 is None:
        return True
    node2_name = static_node_ref_name(
        expr_ir.node2,
        expr_ir.node2_index,
        expr_ir.node2_index2,
    )
    if node2_name is None:
        return False
    node2_slot = node_slots.get(node2_name)
    if node2_slot is None:
        return False
    ops.append(BodyExprOp(BODY_EXPR_READ_NODE, index=node2_slot))
    ops.append(BodyExprOp(BODY_EXPR_SUB))
    return True


def _branch_current_node_name(node1: str, node2: str) -> str:
    return f"@I:{node1}:{node2}"


def _lower_expr_tuple(
    exprs: Iterable[Expr],
    context: LoweringContext,
) -> Optional[Tuple[ExprIR, ...]]:
    lowered = []
    for expr in exprs:
        item = lower_expr(expr, context)
        if item is None:
            return None
        lowered.append(item)
    return tuple(lowered)


def _lower_optional_expr(
    expr: Optional[Expr],
    context: LoweringContext,
) -> Optional[ExprIR]:
    if expr is None:
        return None
    return lower_expr(expr, context)


def _any_missing(lowered: Optional[ExprIR], original: Optional[Expr]) -> bool:
    return original is not None and lowered is None


def _normalize_function_name(name: str) -> str:
    if name in _DOLLAR_MATH_ALIASES:
        return name[1:]
    return name


def _emit_identifier(name: str) -> str:
    if name == "inf":
        return "float('inf')"
    if name in {"$abstime", "$realtime"}:
        return "time_value"
    if name == "$temperature":
        return "(temperature_c + 273.15)"
    if name == "$vt":
        return "(1.380649e-23 * (temperature_c + 273.15) / 1.602176634e-19)"
    return f"var({name!r})"


def _emit_function_call(expr_ir: FunctionCallIR) -> str:
    args = ", ".join(emit_python(arg) for arg in expr_ir.args)
    name = expr_ir.name
    if name == "ln":
        return f"math.log({args})"
    if name == "log":
        return f"math.log10({args})"
    if name in {"exp", "sqrt", "sin", "cos", "tan", "tanh", "floor", "ceil"}:
        return f"math.{name}({args})"
    if name in {"abs", "pow", "min", "max"}:
        return f"{name}({args})"
    if name.startswith("$"):
        helper = "fn_" + name[1:].replace("$", "").replace("-", "_")
        return f"{helper}({args})"
    return f"fn_{name}({args})"


def _emit_branch_access(expr_ir: BranchAccessIR) -> str:
    n1 = _emit_node_ref(expr_ir.node1, expr_ir.node1_index, expr_ir.node1_index2)
    if expr_ir.access_type == "I":
        if expr_ir.node2 is None:
            return f"current({n1})"
        n2 = _emit_node_ref(expr_ir.node2, expr_ir.node2_index, expr_ir.node2_index2)
        return f"current({n1}, {n2})"
    if expr_ir.node2 is None:
        return f"voltage({n1})"
    n2 = _emit_node_ref(expr_ir.node2, expr_ir.node2_index, expr_ir.node2_index2)
    return f"(voltage({n1}) - voltage({n2}))"


def _emit_node_ref(
    name: str,
    index1: Optional[ExprIR],
    index2: Optional[ExprIR],
) -> str:
    if index1 is None:
        return repr(name)
    idx1 = f"int({emit_python(index1)})"
    if index2 is None:
        return f"node_ref({name!r}, {idx1})"
    idx2 = f"int({emit_python(index2)})"
    return f"node_ref({name!r}, {idx1}, {idx2})"
