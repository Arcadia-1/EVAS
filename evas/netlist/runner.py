"""
runner.py -- EVAS runner: parse .scs, compile VA, simulate, produce log + CSV.
"""

import csv
import os
import re as _re
import sys
import time
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

import numpy as np

from evas.build_identity import (
    collect_build_identity,
    package_version,
    write_build_identity,
)
from evas.compiler import ast_nodes as va_ast
from evas.compiler.lexer import TokenType
from evas.compiler.lexer import tokenize as tokenize_va
from evas.compiler.parser import (
    SpectreReservedIdentifierError,
)
from evas.compiler.parser import (
    parse as parse_va,
)
from evas.compiler.parser import (
    parse_all as parse_all_va,
)
from evas.compiler.preprocessor import preprocess
from evas.simulator.backend import compile_module
from evas.simulator.engine import SimResult, Simulator, dc, pulse, pwl, sine, square
from evas.simulator.indexed import (
    build_indexed_run_plan,
    check_indexed_trace_round_trip,
)
from evas.simulator.rust_program import (
    RustSimCompileReport,
    build_source_record_rust_program,
)
from evas.support_tiers import (
    format_support_tier_hint,
    support_tier_for_function,
    unsupported_feature_message,
)

from .spectre_parser import (
    SpectreNetlist,
    SpectreSource,
    _parse_suffix_number,
    has_transistors,
    parse_spectre,
    strict_spectre_netlist_diagnostics,
)

VERSION = package_version()

PYTHON_EVAS_ENGINE = "python"
RUST_EVAS_ENGINE = "evas-rust"
DEFAULT_EVAS_ENGINE = RUST_EVAS_ENGINE
_RUST_ENGINE_ALIASES = {"evas-rust", "evas2", "rust2"}
_DEVELOPER_ENGINE_OVERRIDE: Optional[str] = None
_PYTHON_COMPATIBILITY_FUNCTIONS = frozenset({"absdelay"})

_EVAS_PROFILE_PRESETS = {
    # Focus on runtime.
    "fast": {"refine_factor": 8, "refine_steps": 4, "reltol_min": 5e-3},
    # Keep current default behavior.
    "balanced": {"refine_factor": 16, "refine_steps": 8, "reltol_min": 1e-3},
    # Focus on edge timing / crossing precision.
    "precision": {"refine_factor": 32, "refine_steps": 16, "reltol_max": 1e-4},
}

_SUPPLY_PORT_NAMES = {
    "vdd", "vdda", "vddd", "vcc", "avdd", "dvdd",
    "vss", "vssa", "vssd", "gnd", "gnda", "gndd", "vee",
}

_CONTINUOUS_DISCIPLINE_TOKEN_TYPES = {
    TokenType.ELECTRICAL,
    TokenType.VOLTAGE,
    TokenType.CURRENT,
}


@dataclass(frozen=True)
class SpectreCompileDiagnostic:
    stage: str
    message: str
    severity: str = "error"


@dataclass
class SpectreCompileResult:
    ok: bool
    stage: str
    diagnostics: Tuple[SpectreCompileDiagnostic, ...] = ()
    warnings: int = 0
    errors: int = 0
    netlist: Optional[SpectreNetlist] = None
    simulator: Optional[Simulator] = None
    models_by_name: Dict[str, Tuple[object, object]] = field(default_factory=dict)
    all_nodes: set = field(default_factory=set)
    record_nodes: set = field(default_factory=set)
    required_trace_signals: List[str] = field(default_factory=list)
    explicit_save_signals: List[str] = field(default_factory=list)
    instance_counts: Dict[str, int] = field(default_factory=dict)
    rust_report: Optional[RustSimCompileReport] = None


def _apply_evas_profile(profile: str, refine_factor: int, refine_steps: int, reltol: float):
    p = (profile or "").strip().lower()
    if p not in _EVAS_PROFILE_PRESETS:
        return refine_factor, refine_steps, reltol, ""
    cfg = _EVAS_PROFILE_PRESETS[p]
    rf = int(cfg["refine_factor"])
    rs = int(cfg["refine_steps"])
    rt = float(reltol)
    if "reltol_min" in cfg:
        rt = max(rt, float(cfg["reltol_min"]))
    if "reltol_max" in cfg:
        rt = min(rt, float(cfg["reltol_max"]))
    return rf, rs, rt, p


def _simopt_bool(simopt: Dict[str, object], key: str, default: bool = False) -> bool:
    value = simopt.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _configured_evas_engine(
    simopt: Dict[str, object],
    *,
    developer_engine: Optional[str] = None,
) -> str:
    """Resolve EVAS engine selection.

    EVAS2/Rust is the only production execution engine. Legacy evas2/rust2
    selectors are accepted as compatibility aliases for evas-rust. The private
    developer override keeps internal parity tests available without exposing a
    production fallback through the CLI, environment, or netlist.
    """

    explicit = str(simopt.get("evas_engine", "")).strip().lower()
    if explicit:
        if explicit == PYTHON_EVAS_ENGINE and developer_engine == PYTHON_EVAS_ENGINE:
            return PYTHON_EVAS_ENGINE
        return _normalize_evas_engine(explicit)
    env_engine = os.environ.get("EVAS_ENGINE", "").strip().lower()
    if env_engine:
        if env_engine == PYTHON_EVAS_ENGINE and developer_engine == PYTHON_EVAS_ENGINE:
            return PYTHON_EVAS_ENGINE
        return _normalize_evas_engine(env_engine)
    if developer_engine is not None:
        text = developer_engine.strip().lower()
        if text == PYTHON_EVAS_ENGINE:
            return PYTHON_EVAS_ENGINE
        return _normalize_evas_engine(text)
    return DEFAULT_EVAS_ENGINE


def _normalize_evas_engine(engine: str) -> str:
    text = engine.strip().lower()
    if text in _RUST_ENGINE_ALIASES:
        return RUST_EVAS_ENGINE
    if text == PYTHON_EVAS_ENGINE:
        raise ValueError(
            "the Python simulation engine is not a supported production engine; "
            "install a compatible evas-rust core"
        )
    raise ValueError(
        f"unsupported EVAS engine {engine!r}; expected {RUST_EVAS_ENGINE!r}"
    )


def _netlist_python_compatibility_features(
    netlist: SpectreNetlist,
) -> Tuple[str, ...]:
    """Return supported syntax features that require the Python engine.

    This is a narrow, syntax-driven compatibility route. It never falls back
    because Rust compilation or simulation failed.
    """

    features: set[str] = set()
    source_dir = Path(netlist.source_dir)
    for include in netlist.ahdl_includes:
        include_path = Path(include.path)
        va_path = (
            include_path.resolve()
            if include_path.is_absolute()
            else (source_dir / include_path).resolve()
        )
        if not va_path.is_file():
            continue
        try:
            source = va_path.read_text(encoding="utf-8", errors="replace")
            preprocessed, _defines, _default_transition = preprocess(
                source,
                source_dir=str(va_path.parent),
            )
            tokens = tokenize_va(preprocessed)
            modules = parse_all_va(preprocessed)
        except Exception:
            # The normal compile gate owns diagnostics for malformed sources.
            continue
        for current, following in zip(tokens, tokens[1:]):
            name = str(current.value).lower()
            if (
                current.type == TokenType.IDENT
                and following.type == TokenType.LPAREN
                and name in _PYTHON_COMPATIBILITY_FUNCTIONS
            ):
                features.add(name)
        for module in modules:
            array_names = {
                variable.name
                for variable in module.variables
                if getattr(variable, "is_array", False)
            }
            if array_names:
                static_names = {
                    parameter.name
                    for parameter in va_ast.module_constant_parameters(module)
                }
                static_names.update(
                    variable.name
                    for variable in module.variables
                    if getattr(variable, "is_genvar", False)
                )
                for access in _iter_array_accesses(module):
                    if access.name not in array_names:
                        continue
                    if not _is_static_array_index(access.index, static_names):
                        features.add("dynamic_state_array_access")
                        break
            if _module_uses_continuous_state_cross(module):
                features.add("continuous_state_cross_event")
    return tuple(sorted(features))


def _iter_array_accesses(value):
    if value is None:
        return
    if isinstance(value, va_ast.ArrayAccess):
        yield value
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_array_accesses(item)
    elif is_dataclass(value):
        for field in fields(value):
            yield from _iter_array_accesses(getattr(value, field.name))


def _is_static_array_index(value, static_names: set[str]) -> bool:
    if isinstance(value, va_ast.NumberLiteral):
        return True
    if isinstance(value, va_ast.Identifier):
        return value.name in static_names
    if isinstance(value, (va_ast.UnaryExpr, va_ast.BinaryExpr)):
        return all(
            _is_static_array_index(getattr(value, field.name), static_names)
            for field in fields(value)
            if field.name != "op"
        )
    return False


def _module_uses_continuous_state_cross(module) -> bool:
    """Detect cross() expressions fed by procedural continuous state.

    Rust currently does not guarantee Spectre's ordering for a scalar assigned
    in the continuously evaluated analog body and then consumed by a separate
    cross event in that same body.  Route only this syntactic dependency to the
    compatibility engine; direct cross(V(...)) remains on Rust.
    """

    analog_block = getattr(module, "analog_block", None)
    if analog_block is None:
        return False
    state_names = {
        variable.name
        for variable in module.variables
        if not getattr(variable, "is_array", False)
    }
    continuously_assigned = _continuous_assignment_targets(
        analog_block.body,
        state_names,
    )
    if not continuously_assigned:
        return False
    for value in _iter_dataclass_values(analog_block.body):
        if not isinstance(value, va_ast.EventStatement):
            continue
        events = (
            value.event.events
            if isinstance(value.event, va_ast.CombinedEvent)
            else (value.event,)
        )
        for event in events:
            if event.event_type != va_ast.EventType.CROSS or not event.args:
                continue
            identifiers = {
                item.name
                for item in _iter_dataclass_values(event.args[0])
                if isinstance(item, va_ast.Identifier)
            }
            if identifiers & continuously_assigned:
                return True
    return False


def _continuous_assignment_targets(value, state_names: set[str], in_event=False):
    targets: set[str] = set()
    if value is None:
        return targets
    if isinstance(value, va_ast.EventStatement):
        return _continuous_assignment_targets(value.body, state_names, True)
    if isinstance(value, va_ast.Assignment) and not in_event:
        if isinstance(value.target, va_ast.Identifier):
            if value.target.name in state_names:
                targets.add(value.target.name)
    if isinstance(value, (list, tuple)):
        for item in value:
            targets.update(
                _continuous_assignment_targets(item, state_names, in_event)
            )
    elif is_dataclass(value):
        for field in fields(value):
            targets.update(
                _continuous_assignment_targets(
                    getattr(value, field.name), state_names, in_event
                )
            )
    return targets


def _iter_dataclass_values(value):
    if value is None:
        return
    yield value
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_dataclass_values(item)
    elif is_dataclass(value):
        for field in fields(value):
            yield from _iter_dataclass_values(getattr(value, field.name))


def _first_param(params: Dict[str, object], *keys: str, default: object = None) -> object:
    for key in keys:
        if key in params:
            return params[key]
    return default


# ---------------------------------------------------------------------------
# VA model compilation
# ---------------------------------------------------------------------------

def _compile_va(
    va_path: str,
    source_dir: str = None,
    static_branch_fastpath_codegen: bool = False,
    indexed_state_fastpath_codegen: bool = False,
):
    """Compile a .va file. Returns (ModelClass, Module) tuple."""
    if source_dir is None:
        source_dir = str(Path(va_path).parent)
    source = Path(va_path).read_text(encoding='utf-8', errors='replace')
    pp_src, defines, default_trans = preprocess(source, source_dir=source_dir)
    _validate_defined_disciplines(pp_src)
    module = parse_va(pp_src)
    module.defines = defines
    _validate_va_spectre_compat(module)
    if default_trans is None:
        default_trans = 1e-12
    cls = compile_module(
        module,
        default_trans,
        static_branch_fastpath_codegen=static_branch_fastpath_codegen,
        indexed_state_fastpath_codegen=indexed_state_fastpath_codegen,
    )
    return cls, module


def _compile_va_all(
    va_path: str,
    source_dir: str = None,
    static_branch_fastpath_codegen: bool = False,
    indexed_state_fastpath_codegen: bool = False,
):
    """Compile every module/connectmodule artifact in a .va file."""
    if source_dir is None:
        source_dir = str(Path(va_path).parent)
    source = Path(va_path).read_text(encoding='utf-8', errors='replace')
    pp_src, defines, default_trans = preprocess(source, source_dir=source_dir)
    _validate_defined_disciplines(pp_src)
    modules = parse_all_va(pp_src)
    if default_trans is None:
        default_trans = 1e-12
    compiled = []
    for module in modules:
        module.defines = defines
        _validate_va_spectre_compat(module)
        cls = compile_module(
            module,
            default_trans,
            static_branch_fastpath_codegen=static_branch_fastpath_codegen,
            indexed_state_fastpath_codegen=indexed_state_fastpath_codegen,
        )
        compiled.append((cls, module))
    return compiled


def _validate_defined_disciplines(source: str) -> None:
    """Reject continuous disciplines that were not defined by the VA input."""
    tokens = tokenize_va(source)
    defined = set()
    used = set()
    for index, token in enumerate(tokens):
        if token.type not in _CONTINUOUS_DISCIPLINE_TOKEN_TYPES:
            continue
        name = token.value
        previous = tokens[index - 1] if index else None
        if (
            previous is not None
            and previous.type == TokenType.IDENT
            and previous.value == "discipline"
        ):
            defined.add(name)
        else:
            used.add(name)

    undefined = sorted(used - defined)
    if undefined:
        name = undefined[0]
        raise ValueError(
            "Spectre-incompatible Verilog-A: undefined discipline "
            f"{name!r}; include \"disciplines.vams\" or define the discipline"
        )


def _format_spectre_reserved_identifier_error(
    source: str,
    error: SpectreReservedIdentifierError,
) -> List[str]:
    identifier = str(error.identifier)
    pattern = _re.compile(rf"\b{_re.escape(identifier)}\b")
    source_line = None
    line_no = getattr(getattr(error, "token", None), "line", 1) or 1
    for idx, line in enumerate(source.splitlines(), start=1):
        if pattern.search(line):
            source_line = pattern.sub(f"{identifier}<<--? ", line, count=1)
            line_no = idx
            break
    if source_line is None:
        source_line = identifier + "<<--? "

    if error.reserved_kind == "built-in function":
        msg = (
            f"Identifier \"{identifier}\" is a reserved name for a "
            "built-in function."
        )
        hint = (
            "Use an identifier that is not a reserved name for a built-in "
            "function."
        )
    elif error.reserved_kind == "simulator library function":
        msg = (
            f"Identifier \"{identifier}\" is a reserved name for a simulator "
            "library function."
        )
        hint = (
            "Use an identifier that is not a reserved name for a simulator "
            "library function."
        )
    else:
        msg = f"Identifier \"{identifier}\" is reserved by Spectre/AHDL."
        hint = "Use an identifier that is not reserved by Spectre/AHDL."

    return [
        f"ERROR (VACOMP-2174): \"{source_line}\"",
        f"line {line_no}: {msg}",
        hint,
    ]


def _expr_has_call(expr, call_name: str) -> bool:
    """Return True if an expression tree contains a call by name."""
    if expr is None:
        return False
    if isinstance(expr, va_ast.FunctionCall):
        if expr.name.lower() == call_name.lower():
            return True
        return any(_expr_has_call(arg, call_name) for arg in expr.args)
    if isinstance(expr, va_ast.MethodCall):
        return any(_expr_has_call(arg, call_name) for arg in expr.args)
    if isinstance(expr, va_ast.BinaryExpr):
        return (_expr_has_call(expr.left, call_name) or
                _expr_has_call(expr.right, call_name))
    if isinstance(expr, va_ast.UnaryExpr):
        return _expr_has_call(expr.operand, call_name)
    if isinstance(expr, va_ast.TernaryExpr):
        return (_expr_has_call(expr.cond, call_name) or
                _expr_has_call(expr.true_expr, call_name) or
                _expr_has_call(expr.false_expr, call_name))
    if isinstance(expr, va_ast.ArrayAccess):
        return _expr_has_call(expr.index, call_name)
    if isinstance(expr, va_ast.BranchAccess):
        return (_expr_has_call(expr.node1_index, call_name) or
                _expr_has_call(expr.node2_index, call_name) or
                _expr_has_call(expr.node1_index2, call_name) or
                _expr_has_call(expr.node2_index2, call_name))
    return False


_SUPPORTED_FUNCTION_CALLS = {
    'transition', 'absdelay', 'slew', 'ddt', 'idt', 'idtmod', 'cross', 'last_crossing',
    'limexp',
    'laplace_nd', 'laplace_np', 'laplace_zd', 'laplace_zp',
    'zi_nd', 'zi_np', 'zi_zd', 'zi_zp',
    'ln', 'log', 'exp', 'sqrt', 'abs', 'pow', 'min', 'max',
    'sin', 'cos', 'tan', 'tanh', 'floor', 'ceil',
    '$ln', '$log', '$exp', '$sqrt', '$abs', '$pow', '$min', '$max',
    '$sin', '$cos', '$tan', '$tanh', '$floor', '$ceil',
    '$temperature', '$vt', '$simparam', '$attribute',
    'potential', 'flow',
    '$rtoi', '$param_given', '$port_connected', '$mfactor',
    '$analog_node_alias', '$analog_port_alias',
    '$cds_get_mc_trial_number', '$cds_set_rf_source_info',
    '$cds_violation',
    '$table_model',
    '$rdist_normal', '$rdist_exponential', '$rdist_poisson',
    '$rdist_chi_square', '$rdist_t', '$rdist_erlang',
    '$dist_normal', '$dist_exponential', '$dist_poisson',
    '$dist_chi_square', '$dist_t', '$dist_erlang',
    '$random', '$dist_uniform', '$rdist_uniform',
    '$fopen', '$fclose', '$fwrite', '$fstrobe', '$strobe',
    '$display', '$debug', '$warning', '$error', '$info',
    '$fscanf', '$sscanf', '$fgets', '$feof', '$fseek', '$ftell', '$rewind',
    '$swrite', '$sformat',
    'analysis', 'ac_stim', 'white_noise', 'flicker_noise', 'noise_table',
    '$abstime', '$realtime', '$bound_step', '$discontinuity',
}


def _iter_expr_calls(expr):
    """Yield FunctionCall nodes from an expression tree."""
    if expr is None:
        return
    if isinstance(expr, va_ast.FunctionCall):
        yield expr
        for arg in expr.args:
            yield from _iter_expr_calls(arg)
    elif isinstance(expr, va_ast.MethodCall):
        for arg in expr.args:
            yield from _iter_expr_calls(arg)
    elif isinstance(expr, va_ast.BinaryExpr):
        yield from _iter_expr_calls(expr.left)
        yield from _iter_expr_calls(expr.right)
    elif isinstance(expr, va_ast.UnaryExpr):
        yield from _iter_expr_calls(expr.operand)
    elif isinstance(expr, va_ast.TernaryExpr):
        yield from _iter_expr_calls(expr.cond)
        yield from _iter_expr_calls(expr.true_expr)
        yield from _iter_expr_calls(expr.false_expr)
    elif isinstance(expr, va_ast.ArrayAccess):
        yield from _iter_expr_calls(expr.index)
    elif isinstance(expr, va_ast.BranchAccess):
        yield from _iter_expr_calls(expr.node1_index)
        yield from _iter_expr_calls(expr.node2_index)
        yield from _iter_expr_calls(expr.node1_index2)
        yield from _iter_expr_calls(expr.node2_index2)


def _assignment_target_name(assign) -> Optional[str]:
    target = getattr(assign, "target", None)
    if isinstance(target, va_ast.Identifier):
        return target.name
    if isinstance(target, va_ast.ArrayAccess):
        return target.name
    return None


def _validate_transition_statement(stmt, conditional_depth: int = 0,
                                   genvar_names: Optional[set] = None,
                                   in_event: bool = False,
                                   user_function_names: Optional[set] = None) -> None:
    """Reject Verilog-A structures known to diverge from Spectre VACOMP."""
    if genvar_names is None:
        genvar_names = set()
    if user_function_names is None:
        user_function_names = set()
    if stmt is None:
        return
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            _validate_transition_statement(child, conditional_depth, genvar_names, in_event, user_function_names)
        return
    if isinstance(stmt, va_ast.Contribution):
        branch_indices = (
            stmt.branch.node1_index,
            stmt.branch.node2_index,
            stmt.branch.node1_index2,
            stmt.branch.node2_index2,
        )
        if any(
            index is not None and not _spectre_static_index(index, genvar_names)
            for index in branch_indices
        ):
            raise ValueError(
                "Spectre-incompatible Verilog-A: runtime-indexed analog vector "
                "contribution; use a genvar/analog_for static index"
            )
        if in_event:
            raise ValueError(
                "Spectre-incompatible Verilog-A: contribution statement "
                "is embedded in an analog event body"
            )
        if conditional_depth > 0 and _expr_has_call(stmt.expr, "transition"):
            raise ValueError(
                "Spectre-incompatible Verilog-A: transition() contribution "
                "is inside a conditional/event/loop/case statement"
            )
        _validate_supported_function_calls(stmt.expr, user_function_names)
        return
    if isinstance(stmt, va_ast.Assignment):
        if isinstance(stmt.target, va_ast.FunctionCall):
            raise ValueError(
                "Spectre-incompatible Verilog-A: standalone function call "
                f"{stmt.target.name}() is not a supported procedural statement"
            )
        if conditional_depth > 0 and _expr_has_call(stmt.value, "transition"):
            raise ValueError(
                "Spectre-incompatible Verilog-A: transition() expression "
                "is inside a conditional/event/loop/case statement"
            )
        _validate_supported_function_calls(stmt.target, user_function_names)
        _validate_supported_function_calls(stmt.value, user_function_names)
        return
    if isinstance(stmt, va_ast.SystemTask):
        for arg in stmt.args:
            _validate_supported_function_calls(arg, user_function_names)
        return
    if isinstance(stmt, va_ast.TaskCall):
        for arg in stmt.args:
            _validate_supported_function_calls(arg, user_function_names)
        return
    if isinstance(stmt, va_ast.EventStatement):
        if getattr(stmt, "analog_initial", False) and _contains_branch_access(
            stmt.body
        ):
            raise ValueError(
                "Spectre-incompatible Verilog-A: nature access inside an "
                "analog initial block"
            )
        event_is_initial_step = (
            isinstance(stmt.event, va_ast.EventExpr)
            and stmt.event.event_type == va_ast.EventType.INITIAL_STEP
        )
        _validate_transition_statement(
            stmt.body,
            conditional_depth + 1,
            genvar_names,
            False if event_is_initial_step else True,
            user_function_names,
        )
        return
    if isinstance(stmt, va_ast.IfStatement):
        _validate_supported_function_calls(stmt.cond, user_function_names)
        _validate_transition_statement(stmt.then_body, conditional_depth + 1, genvar_names, in_event, user_function_names)
        _validate_transition_statement(stmt.else_body, conditional_depth + 1, genvar_names, in_event, user_function_names)
        return
    if isinstance(stmt, va_ast.ForStatement):
        loop_var = _assignment_target_name(stmt.init)
        loop_depth = conditional_depth if loop_var in genvar_names else conditional_depth + 1
        _validate_transition_statement(stmt.init, conditional_depth, genvar_names, in_event, user_function_names)
        _validate_supported_function_calls(stmt.cond, user_function_names)
        _validate_transition_statement(stmt.update, conditional_depth, genvar_names, in_event, user_function_names)
        _validate_transition_statement(stmt.body, loop_depth, genvar_names, in_event, user_function_names)
        return
    if isinstance(stmt, va_ast.WhileStatement):
        _validate_supported_function_calls(stmt.cond, user_function_names)
        _validate_transition_statement(stmt.body, conditional_depth + 1, genvar_names, in_event, user_function_names)
        return
    if isinstance(stmt, va_ast.CaseStatement):
        _validate_supported_function_calls(stmt.expr, user_function_names)
        for item in stmt.items:
            for value in item.values:
                _validate_supported_function_calls(value, user_function_names)
            _validate_transition_statement(item.body, conditional_depth + 1, genvar_names, in_event, user_function_names)


def _contains_branch_access(value) -> bool:
    if isinstance(value, va_ast.BranchAccess):
        return True
    if isinstance(value, (list, tuple)):
        return any(_contains_branch_access(item) for item in value)
    if is_dataclass(value):
        return any(
            _contains_branch_access(getattr(value, field.name))
            for field in fields(value)
        )
    return False


def _iter_value_expressions(stmt):
    """Yield expressions evaluated as values by a procedural statement."""
    if stmt is None:
        return
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            yield from _iter_value_expressions(child)
    elif isinstance(stmt, va_ast.Assignment):
        yield stmt.value
        if isinstance(stmt.target, va_ast.ArrayAccess):
            yield stmt.target.index
            yield stmt.target.index2
    elif isinstance(stmt, va_ast.Contribution):
        yield stmt.expr
        yield stmt.branch.node1_index
        yield stmt.branch.node2_index
        yield stmt.branch.node1_index2
        yield stmt.branch.node2_index2
    elif isinstance(stmt, va_ast.EventStatement):
        events = (
            stmt.event.events
            if isinstance(stmt.event, va_ast.CombinedEvent)
            else [stmt.event]
        )
        for event in events:
            yield from event.args
            yield event.time_tol_expr
            yield event.expr_tol_expr
        yield from _iter_value_expressions(stmt.body)
    elif isinstance(stmt, va_ast.IfStatement):
        yield stmt.cond
        yield from _iter_value_expressions(stmt.then_body)
        yield from _iter_value_expressions(stmt.else_body)
    elif isinstance(stmt, va_ast.ForStatement):
        yield from _iter_value_expressions(stmt.init)
        yield stmt.cond
        yield from _iter_value_expressions(stmt.update)
        yield from _iter_value_expressions(stmt.body)
    elif isinstance(stmt, va_ast.WhileStatement):
        yield stmt.cond
        yield from _iter_value_expressions(stmt.body)
    elif isinstance(stmt, va_ast.CaseStatement):
        yield stmt.expr
        for item in stmt.items:
            yield from item.values
            yield from _iter_value_expressions(item.body)
    elif isinstance(stmt, (va_ast.SystemTask, va_ast.TaskCall)):
        yield va_ast.FunctionCall(name=stmt.name, args=stmt.args)


_NODE_ARGUMENT_FUNCTIONS = {
    "potential": {0, 1},
    "flow": {0, 1},
    "$analog_node_alias": {0, 1},
    "$analog_port_alias": {0, 1},
    "$port_connected": {0},
}


def _bare_continuous_node(
    expr,
    node_names: set,
    *,
    allow_node_ref: bool = False,
) -> Optional[str]:
    """Return a continuous node read without V()/I(), if present."""
    if expr is None:
        return None
    if isinstance(expr, va_ast.Identifier):
        if expr.name in node_names:
            return None if allow_node_ref else expr.name
        return None
    if isinstance(expr, (va_ast.ArrayAccess, va_ast.PartSelect)):
        if expr.name in node_names:
            return None if allow_node_ref else expr.name
    if isinstance(expr, va_ast.MethodCall) and expr.obj in node_names:
        return expr.obj
    if isinstance(expr, va_ast.BranchAccess):
        children = (
            expr.node1_index,
            expr.node2_index,
            expr.node1_index2,
            expr.node2_index2,
        )
    elif isinstance(expr, va_ast.BinaryExpr):
        children = (expr.left, expr.right)
    elif isinstance(expr, va_ast.UnaryExpr):
        children = (expr.operand,)
    elif isinstance(expr, va_ast.TernaryExpr):
        children = (expr.cond, expr.true_expr, expr.false_expr)
    elif isinstance(expr, va_ast.ConcatExpr):
        children = tuple(expr.parts)
    elif isinstance(expr, va_ast.ReplicateExpr):
        children = (expr.count, expr.expr)
    elif isinstance(expr, va_ast.FunctionCall):
        allowed_node_args = _NODE_ARGUMENT_FUNCTIONS.get(expr.name.lower(), set())
        for index, arg in enumerate(expr.args):
            bare_node = _bare_continuous_node(
                arg,
                node_names,
                allow_node_ref=index in allowed_node_args,
            )
            if bare_node is not None:
                return bare_node
        return None
    elif isinstance(expr, va_ast.MethodCall):
        children = tuple(expr.args)
    elif isinstance(expr, va_ast.ArrayAccess):
        children = (expr.index, expr.index2)
    elif isinstance(expr, va_ast.PartSelect):
        children = (expr.msb, expr.lsb)
    else:
        children = ()
    for child in children:
        bare_node = _bare_continuous_node(child, node_names)
        if bare_node is not None:
            return bare_node
    return None


def _validate_no_bare_continuous_nodes(module) -> None:
    node_names = {
        port.name
        for port in module.port_decls
        if port.discipline in {"electrical", "voltage", "current"}
    }
    statement_roots = []
    if module.analog_block is not None:
        statement_roots.append(module.analog_block.body)
    statement_roots.extend(module.continuous_assigns)
    statement_roots.extend(module.always_blocks)
    statement_roots.extend(fn.body for fn in module.functions)
    statement_roots.extend(task.body for task in module.tasks)
    for root in statement_roots:
        for expr in _iter_value_expressions(root):
            bare_node = _bare_continuous_node(expr, node_names)
            if bare_node is not None:
                raise ValueError(
                    "Spectre-incompatible Verilog-A: electrical node "
                    f"{bare_node!r} used as a scalar expression; use "
                    f"V({bare_node})"
                )


def _iter_branch_accesses(value):
    """Yield branch-access expressions from a Verilog-A AST subtree."""
    if value is None:
        return
    if isinstance(value, va_ast.BranchAccess):
        yield value
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_branch_accesses(item)
    elif is_dataclass(value):
        for field in fields(value):
            yield from _iter_branch_accesses(getattr(value, field.name))


def _validate_scalar_electrical_references(module) -> None:
    """Reject scalar values used where Spectre requires electrical terminals."""
    scalar_names = {
        param.name for param in va_ast.module_constant_parameters(module)
    }
    scalar_names.update(
        variable.name
        for variable in module.variables
        if not variable.is_array and not variable.is_vector
    )
    if not scalar_names:
        return

    statement_roots = []
    if module.analog_block is not None:
        statement_roots.append(module.analog_block.body)
    statement_roots.extend(module.continuous_assigns)
    statement_roots.extend(module.always_blocks)
    statement_roots.extend(fn.body for fn in module.functions)
    statement_roots.extend(task.body for task in module.tasks)
    for root in statement_roots:
        for branch in _iter_branch_accesses(root):
            for node_name in (branch.node1, branch.node2):
                if node_name is not None and node_name in scalar_names:
                    raise ValueError(
                        "Spectre-incompatible Verilog-A: parameter/variable "
                        "as an electrical branch node; "
                        f"{node_name!r} is a scalar value, not an electrical node"
                    )

    for instance in module.instances:
        for connection in instance.connections:
            expr = connection.expr
            if (
                isinstance(expr, (va_ast.Identifier, va_ast.ArrayAccess))
                and expr.name in scalar_names
            ):
                raise ValueError(
                    "Spectre-incompatible Verilog-A: parameter/variable "
                    "as an instance terminal; "
                    f"{expr.name!r} is a scalar value, not an electrical terminal"
                )


def _spectre_static_index(expr, static_names: set) -> bool:
    if isinstance(expr, va_ast.NumberLiteral):
        return True
    if isinstance(expr, va_ast.Identifier):
        return expr.name in static_names
    if isinstance(expr, va_ast.UnaryExpr):
        return _spectre_static_index(expr.operand, static_names)
    if isinstance(expr, va_ast.BinaryExpr):
        return (
            _spectre_static_index(expr.left, static_names)
            and _spectre_static_index(expr.right, static_names)
        )
    return False


def _validate_supported_function_calls(expr, user_function_names: Optional[set] = None) -> None:
    if user_function_names is None:
        user_function_names = set()
    for call in _iter_expr_calls(expr):
        if call.name not in _SUPPORTED_FUNCTION_CALLS and call.name not in user_function_names:
            support_tier = support_tier_for_function(call.name)
            raise ValueError(
                unsupported_feature_message(
                    f"{call.name}()",
                    support_tier,
                    "no EVAS behavioral implementation is registered for "
                    "this function/operator",
                )
                + format_support_tier_hint(support_tier)
            )
        _validate_absdelay_call(call)


def _static_numeric_literal(expr) -> Optional[float]:
    if isinstance(expr, va_ast.NumberLiteral):
        return float(expr.value)
    if isinstance(expr, va_ast.UnaryExpr) and expr.op in {"+", "-"}:
        value = _static_numeric_literal(expr.operand)
        if value is None:
            return None
        return value if expr.op == "+" else -value
    return None


def _validate_absdelay_call(call) -> None:
    if call.name.lower() != "absdelay":
        return
    if len(call.args) not in {2, 3}:
        raise ValueError(
            "unsupported Verilog-A feature: absdelay() expects 2 or 3 "
            "arguments in EVAS"
        )
    delay = _static_numeric_literal(call.args[1])
    if delay is not None and delay < 0.0:
        raise ValueError(
            "unsupported Verilog-A feature: absdelay() delay must be "
            "nonnegative"
        )


def _iter_contributions(stmt):
    """Yield Contribution nodes from a statement tree."""
    if stmt is None:
        return
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            yield from _iter_contributions(child)
    elif isinstance(stmt, va_ast.Contribution):
        yield stmt
    elif isinstance(stmt, va_ast.EventStatement):
        yield from _iter_contributions(stmt.body)
    elif isinstance(stmt, va_ast.IfStatement):
        yield from _iter_contributions(stmt.then_body)
        yield from _iter_contributions(stmt.else_body)
    elif isinstance(stmt, va_ast.ForStatement):
        yield from _iter_contributions(stmt.body)
    elif isinstance(stmt, va_ast.WhileStatement):
        yield from _iter_contributions(stmt.body)
    elif isinstance(stmt, va_ast.CaseStatement):
        for item in stmt.items:
            yield from _iter_contributions(item.body)


def _contributed_voltage_ports(module) -> set:
    """Collect Verilog-A port names driven by V(port) <+ contributions."""
    if module.analog_block is None:
        return set()
    ports = set(module.ports)
    driven = set()
    for contrib in _iter_contributions(module.analog_block.body):
        branch = contrib.branch
        if branch.access_type.upper() != "V":
            continue
        if branch.node1 in ports:
            driven.add(branch.node1)
    return driven


def _validate_va_spectre_compat(module) -> None:
    """Run small Spectre-compatibility checks that EVAS can validate locally."""
    ports = set(module.ports)
    for param in va_ast.module_constant_parameters(module):
        if param.name in ports:
            raise ValueError(
                "Spectre-incompatible Verilog-A: parameter name "
                f"{param.name!r} collides with module port in {module.name!r}"
            )
    _validate_no_bare_continuous_nodes(module)
    _validate_scalar_electrical_references(module)
    for warning in getattr(module, "warnings", []):
        if warning.startswith("EVAS-SPECTRE-NONANSI-COMBINED-PORT:"):
            raise ValueError(
                "Spectre-incompatible Verilog-A: use separate direction and "
                "discipline declarations in a non-ANSI module body"
            )
    if module.analog_block is not None:
        genvar_names = {
            v.name for v in module.variables if getattr(v, "is_genvar", False)
        }
        genvar_names.update(
            param.name for param in va_ast.module_constant_parameters(module)
        )
        user_function_names = {fn.name for fn in getattr(module, "functions", [])}
        _validate_transition_statement(
            module.analog_block.body,
            genvar_names=genvar_names,
            user_function_names=user_function_names,
        )


def _source_constrained_nodes(netlist: SpectreNetlist) -> set:
    nodes = set()
    for src in netlist.sources:
        if src.kind != "voltage":
            continue
        nodes.add(src.node_pos)
        if src.node_neg != netlist.ground:
            nodes.add(src.node_neg)
    nodes.discard(netlist.ground)
    return nodes


def _validate_supply_drive_conflicts(instance, module, node_map: Dict[str, str],
                                     source_nodes: set) -> None:
    """Reject a common Spectre rigid-branch-loop pattern.

    A behavioral module should not hard-drive supply-like ports that are already
    constrained by external voltage sources in the testbench.
    """
    for port in _contributed_voltage_ports(module):
        if port.lower() not in _SUPPLY_PORT_NAMES:
            continue
        ext_node = node_map.get(port)
        if ext_node in source_nodes:
            raise ValueError(
                f"instance {instance.name} of {module.name} drives supply port "
                f"{port!r} mapped to externally sourced node {ext_node!r}"
            )


def _expanded_port_count(module) -> int:
    """Count positional instance terminals after expanding vector ports."""
    decl_by_name = {pd.name: pd for pd in module.port_decls}
    count = 0
    for port_name in module.ports:
        pd = decl_by_name.get(port_name)
        if pd and pd.is_array:
            hi = pd.array_hi if pd.array_hi is not None else 0
            lo = pd.array_lo if pd.array_lo is not None else 0
            count += abs(hi - lo) + 1
        else:
            count += 1
    return count


def _validate_instance_arity(instance, module) -> None:
    expected = _expanded_port_count(module)
    actual = len(instance.nodes)
    if actual != expected:
        raise ValueError(
            f"terminal count mismatch for instance {instance.name} of {module.name}: "
            f"{actual} provided, {expected} expected"
        )


def _validate_instance_parameter_overrides(
    instance,
    module,
    *,
    models_by_name: Optional[Dict[str, Tuple[object, object]]] = None,
) -> List[str]:
    unknown: List[str] = []
    allowed = {param.name.lower() for param in module.parameters}
    allowed.add("m")
    for name in instance.params:
        lower_name = str(name).lower()
        if lower_name in allowed:
            continue
        if _module_descendant_declares_parameter(
            module,
            lower_name,
            models_by_name or {},
        ):
            raise ValueError(
                f"inherited-parameter override {name!r} for instance {instance.name} "
                f"of {module.name}"
            )
        unknown.append(str(name))
    return unknown


def _module_descendant_declares_parameter(
    module,
    param_name: str,
    models_by_name: Dict[str, Tuple[object, object]],
) -> bool:
    """Return True when a transitive child module declares the parameter."""

    visited = set()

    def visit(mod) -> bool:
        for child in getattr(mod, "instances", ()) or ():
            child_name = getattr(child, "module_name", None)
            if child_name is None or child_name in visited:
                continue
            visited.add(child_name)
            entry = models_by_name.get(child_name)
            if entry is None:
                continue
            child_mod = entry[1]
            child_params = {
                str(param.name).lower()
                for param in child_mod.parameters
            }
            if param_name in child_params:
                return True
            if visit(child_mod):
                return True
        return False

    return visit(module)


def _validate_inert_resistor_load(
    instance,
    ground: str,
    ideal_voltage_nodes: set,
) -> float:
    """Validate a resistor that is safe in EVAS's voltage-only load subset.

    EVAS does not yet solve arbitrary passive networks.  A two-terminal,
    positive resistor from a behavioral voltage output to ground is nonetheless
    a common Spectre testbench load and does not alter the ideal voltage
    contribution that EVAS computes.  Accept that bounded subset explicitly
    instead of reporting a missing Verilog-A model.
    """
    if len(instance.nodes) != 2:
        raise ValueError(
            f"resistor load {instance.name} must have exactly two terminals"
        )
    if ground not in instance.nodes:
        raise ValueError(
            f"resistor load {instance.name} is unsupported unless one terminal "
            f"is ground {ground!r}"
        )
    resistance = instance.params.get("r")
    if resistance is None:
        raise ValueError(f"resistor load {instance.name} requires r=<ohms>")
    try:
        resistance = float(resistance)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"resistor load {instance.name} has invalid r={resistance!r}"
        ) from exc
    if resistance <= 0.0:
        raise ValueError(
            f"resistor load {instance.name} requires a positive resistance"
        )
    load_nodes = [node for node in instance.nodes if node != ground]
    if len(load_nodes) != 1 or load_nodes[0] not in ideal_voltage_nodes:
        raise ValueError(
            f"resistor load {instance.name} node is not explicitly "
            "ideal-voltage driven"
        )
    return resistance


# ---------------------------------------------------------------------------
# Build node_map from instance and module port declarations
# ---------------------------------------------------------------------------

def _build_node_map(instance, module) -> Dict[str, str]:
    """Map VA port names to netlist node names using positional matching.

    Uses module.ports for ordering (matches the module declaration order),
    and port_decls for array info.

    For array ports (e.g. DOUT[17:0]), the netlist supplies one node per
    array element in order from high index to low index.
    """
    decl_by_name = {pd.name: pd for pd in module.port_decls}
    node_map = {}
    ni = 0
    netlist_nodes = list(instance.nodes)

    for port_name in module.ports:
        pd = decl_by_name.get(port_name)
        if pd and pd.is_array:
            hi = pd.array_hi if pd.array_hi is not None else 0
            lo = pd.array_lo if pd.array_lo is not None else 0
            indices = range(hi, lo - 1, -1) if hi >= lo else range(hi, lo + 1)
            for idx in indices:
                if ni < len(netlist_nodes):
                    node_map[f'{pd.name}[{idx}]'] = netlist_nodes[ni]
                    ni += 1
        else:
            if ni < len(netlist_nodes):
                node_map[port_name] = netlist_nodes[ni]
                ni += 1

    return node_map


def _ideal_voltage_driven_nodes(netlist: SpectreNetlist, models_by_name: Dict) -> set:
    """Collect nodes whose voltage is fixed without solving a passive network."""
    nodes = _source_constrained_nodes(netlist)
    for instance in netlist.instances:
        compiled = models_by_name.get(instance.model_name)
        if compiled is None:
            continue
        _cls, module = compiled
        if len(instance.nodes) != _expanded_port_count(module):
            continue
        node_map = _build_node_map(instance, module)
        for port in _contributed_voltage_ports(module):
            node = node_map.get(port)
            if node is not None and node != netlist.ground:
                nodes.add(node)
    return nodes


# ---------------------------------------------------------------------------
# Source conversion: Spectre params -> simulator waveform
# ---------------------------------------------------------------------------

def _combined_waveform(
    reference: Callable[[float], float],
    delta: Callable[[float], float],
) -> Callable[[float], float]:
    def waveform(t: float) -> float:
        return reference(t) + delta(t)

    def _next_breakpoint(t: float) -> Optional[float]:
        breakpoints: List[float] = []
        for wave in (reference, delta):
            next_breakpoint = getattr(wave, "_next_breakpoint", None)
            if next_breakpoint is None:
                continue
            value = next_breakpoint(t)
            if value is not None:
                breakpoints.append(value)
        return min(breakpoints) if breakpoints else None

    waveform._next_breakpoint = _next_breakpoint  # type: ignore[attr-defined]
    metadata = _combined_waveform_metadata(reference, delta)
    if metadata is not None:
        waveform._evas_waveform = metadata  # type: ignore[attr-defined]
    return waveform


def _negated_waveform(
    waveform: Callable[[float], float],
) -> Callable[[float], float]:
    """Return ``-waveform`` while preserving event-breakpoint metadata."""

    def negated(t: float) -> float:
        return -waveform(t)

    next_breakpoint = getattr(waveform, "_next_breakpoint", None)
    if next_breakpoint is not None:
        negated._next_breakpoint = next_breakpoint  # type: ignore[attr-defined]

    metadata = getattr(waveform, "_evas_waveform", None)
    if isinstance(metadata, dict):
        kind = str(metadata.get("kind", ""))
        negated_metadata = dict(metadata)
        if kind == "dc":
            negated_metadata["voltage"] = -float(
                negated_metadata.get("voltage", 0.0) or 0.0
            )
        elif kind in {"pulse", "square"}:
            negated_metadata["v_lo"] = -float(
                negated_metadata.get("v_lo", 0.0) or 0.0
            )
            negated_metadata["v_hi"] = -float(
                negated_metadata.get("v_hi", 0.0) or 0.0
            )
        elif kind == "sine":
            negated_metadata["offset"] = -float(
                negated_metadata.get("offset", 0.0) or 0.0
            )
            negated_metadata["amplitude"] = -float(
                negated_metadata.get("amplitude", 0.0) or 0.0
            )
        elif kind == "pwl":
            negated_metadata["values"] = tuple(
                -float(value)
                for value in negated_metadata.get("values", ()) or ()
            )
        else:
            negated_metadata = None
        if negated_metadata is not None:
            negated._evas_waveform = negated_metadata  # type: ignore[attr-defined]

    return negated


def _offset_waveform_metadata(metadata, offset: float):
    kind = str(metadata.get("kind", ""))
    shifted = dict(metadata)
    if kind == "dc":
        shifted["voltage"] = float(shifted.get("voltage", 0.0) or 0.0) + offset
        return shifted
    if kind in {"pulse", "square"}:
        shifted["v_lo"] = float(shifted.get("v_lo", 0.0) or 0.0) + offset
        shifted["v_hi"] = float(shifted.get("v_hi", 0.0) or 0.0) + offset
        return shifted
    if kind == "sine":
        shifted["offset"] = float(shifted.get("offset", 0.0) or 0.0) + offset
        return shifted
    if kind == "pwl":
        shifted["values"] = tuple(
            float(value) + offset
            for value in shifted.get("values", ()) or ()
        )
        return shifted
    return None


def _combined_waveform_metadata(
    reference: Callable[[float], float],
    delta: Callable[[float], float],
):
    reference_meta = getattr(reference, "_evas_waveform", None)
    delta_meta = getattr(delta, "_evas_waveform", None)
    if not isinstance(reference_meta, dict) or not isinstance(delta_meta, dict):
        return None
    reference_kind = str(reference_meta.get("kind", ""))
    delta_kind = str(delta_meta.get("kind", ""))
    if reference_kind == "dc":
        return _offset_waveform_metadata(
            delta_meta,
            float(reference_meta.get("voltage", 0.0) or 0.0),
        )
    if delta_kind == "dc":
        return _offset_waveform_metadata(
            reference_meta,
            float(delta_meta.get("voltage", 0.0) or 0.0),
        )
    return None


def _spectre_voltage_waveform(
    src: SpectreSource,
) -> Tuple[Callable[[float], float], List[str]]:
    """Build the source's relative voltage waveform.

    The returned waveform is V(node_pos, node_neg), not necessarily V(node_pos).
    """
    params = src.params
    stype = src.source_type
    warn: List[str] = []

    if stype in ('dc', ''):
        return dc(float(params.get('dc', 0.0))), warn

    elif stype == 'pulse':
        v0 = float(params.get('val0', 0.0))
        v1 = float(params.get('val1', 1.0))
        period = float(params.get('period', 0.0))

        if v0 == v1:
            warn.append(f"{src.name}: pulse val0 == val1 == {v0} "
                        f"— treated as DC {v0} V")
            return dc(v0), warn

        delay = float(params.get('delay', 0.0))
        rise = float(params.get('rise', 1e-12))
        fall = float(params.get('fall', 1e-12))
        width = params.get('width', None)
        if period <= 0:
            warn.append(f"{src.name}: pulse period not set "
                        "- treated as nonperiodic one-shot pulse")
        duty = float(width) / period if width is not None and period > 0 else 0.5

        return pulse(
            v_lo=v0, v_hi=v1, period=period, duty=duty,
            rise=rise, fall=fall, delay=delay,
            width=float(width) if width is not None else None,
        ), warn

    elif stype == 'pwl':
        wave = params.get('wave', '')
        if isinstance(wave, list):
            vals = wave
        elif isinstance(wave, str) and wave:
            vals = [_parse_suffix_number(t) for t in wave.split()]
            vals = [v for v in vals if v is not None]
        else:
            vals = []
        if not vals:
            raise ValueError(f"{src.name}: PWL wave must contain at least one time/value pair")
        if len(vals) % 2 != 0:
            raise ValueError(f"{src.name}: PWL wave must contain an even number of values")
        times = vals[0::2]
        values = vals[1::2]
        return pwl(times, values), warn

    elif stype in ('sin', 'sine'):
        # Match Spectre transient vsource sine semantics for the supported
        # subset.  `sinedc`/`ampl` are the canonical transient parameters; do
        # not treat small-signal or schematic convenience names such as
        # vo/va/offset/amplitude as transient aliases.
        offset = float(_first_param(
            params,
            'sinedc', 'dc',
            default=0.0,
        ))
        ampl = float(_first_param(
            params,
            'ampl', 'mag',
            default=1.0,
        ))
        freq = float(_first_param(
            params,
            'freq',
            default=0.0,
        ))
        phase = float(_first_param(
            params,
            'phase', 'sinephase', 'phi',
            default=0.0,
        ))

        if freq <= 0:
            warn.append(f"{src.name}: sine freq not set "
                        f"— treated as DC {offset} V")
            return dc(offset), warn

        if ampl == 0:
            warn.append(f"{src.name}: sine amplitude=0 — treated as DC {offset} V")
            return dc(offset), warn

        return sine(offset=offset, amplitude=ampl, freq=freq, phase=phase), warn

    elif stype == 'square':
        # Spectre vsource type=square: val0/val1 low/high plateaus, period,
        # delay, rise, fall ramps, and width (high plateau after the rise).
        v0 = float(params.get('val0', 0.0))
        v1 = float(params.get('val1', 1.0))
        period = float(params.get('period', 0.0))

        if v0 == v1:
            warn.append(f"{src.name}: square val0 == val1 == {v0} "
                        f"— treated as DC {v0} V")
            return dc(v0), warn

        delay = float(params.get('delay', 0.0))
        rise = float(params.get('rise', 1e-12))
        fall = float(params.get('fall', 1e-12))
        width = params.get('width', None)
        if period <= 0 and width is None:
            warn.append(f"{src.name}: square period not set "
                        "- treated as nonperiodic one-shot square pulse")

        return square(
            v_lo=v0, v_hi=v1, period=period, delay=delay,
            rise=rise, fall=fall,
            width=float(width) if width is not None else None,
        ), warn

    else:
        # Fail loudly on unsupported source types instead of silently dropping
        # the source (which would leave the driven node stuck at its initial
        # value and make any @cross on it never fire).  This mirrors the
        # isource path above and keeps unsupported Spectre constructs from
        # producing misleading successful simulations.
        raise ValueError(f"{src.name}: unsupported vsource type={stype!r}")


def _add_spectre_current_source(sim: Simulator, src: SpectreSource) -> List[str]:
    params = src.params
    stype = src.source_type
    warn: List[str] = []

    if stype in ('dc', ''):
        sim.add_current_source(src.node_pos, src.node_neg, dc(float(params.get('dc', 0.0))))
        return warn
    if stype == 'pwl':
        wave = params.get('wave', '')
        if isinstance(wave, list):
            vals = wave
        elif isinstance(wave, str) and wave:
            vals = [_parse_suffix_number(t) for t in wave.split()]
            vals = [v for v in vals if v is not None]
        else:
            vals = []
        if not vals:
            raise ValueError(f"{src.name}: PWL wave must contain at least one time/value pair")
        if len(vals) % 2 != 0:
            raise ValueError(f"{src.name}: PWL wave must contain an even number of values")
        sim.add_current_source(src.node_pos, src.node_neg, pwl(vals[0::2], vals[1::2]))
        return warn
    raise ValueError(f"{src.name}: unsupported isource type={stype!r}")


def _add_spectre_sources(
    sim: Simulator,
    sources: List[SpectreSource],
    ground: str,
) -> List[str]:
    """Convert Spectre sources to simulator source constraints.

    Voltage sources are differential in Spectre.  The simulator stores absolute
    node waveforms, so a source with a non-ground negative terminal must be
    offset by the resolved waveform of that negative terminal.
    """
    warn: List[str] = []
    voltage_sources: List[SpectreSource] = []
    relative_waveforms: Dict[int, Callable[[float], float]] = {}

    for src in sources:
        if getattr(src, "kind", "voltage") == "current":
            warn.extend(_add_spectre_current_source(sim, src))
            continue
        waveform, src_warnings = _spectre_voltage_waveform(src)
        warn.extend(src_warnings)
        voltage_sources.append(src)
        relative_waveforms[id(src)] = waveform

    ground_waveform = dc(0.0)
    resolved_by_node: Dict[str, Callable[[float], float]] = {ground: ground_waveform}

    # A Spectre vsource constrains V(pos) - V(neg), so either terminal can be
    # resolved from an already anchored terminal.  Walking only from the
    # positive terminal fails for valid sources such as ``(0 signal)`` and
    # incorrectly leaves ``signal`` at zero instead of driving ``-Vsource``.
    pending = list(voltage_sources)
    while pending:
        next_pending: List[SpectreSource] = []
        made_progress = False
        for src in pending:
            relative = relative_waveforms[id(src)]
            pos_resolved = src.node_pos in resolved_by_node
            neg_resolved = src.node_neg in resolved_by_node
            if neg_resolved and not pos_resolved:
                resolved_by_node[src.node_pos] = _combined_waveform(
                    resolved_by_node[src.node_neg],
                    relative,
                )
                made_progress = True
            elif pos_resolved and not neg_resolved:
                resolved_by_node[src.node_neg] = _combined_waveform(
                    resolved_by_node[src.node_pos],
                    _negated_waveform(relative),
                )
                made_progress = True
            elif not pos_resolved and not neg_resolved:
                next_pending.append(src)
        if not made_progress:
            if next_pending:
                unresolved_references = sorted(
                    {
                        src.node_neg
                        for src in next_pending
                        if src.node_neg not in resolved_by_node
                    }
                )
                unresolved = sorted(
                    {
                        node
                        for src in next_pending
                        for node in (src.node_pos, src.node_neg)
                        if node not in resolved_by_node
                    }
                )
                node = (
                    unresolved_references[0]
                    if unresolved_references
                    else unresolved[0]
                )
                resolved_by_node[node] = ground_waveform
                warn.append(
                    "No ground-referenced voltage-source path for "
                    f"{node!r}; anchoring the floating source island to "
                    f"{ground!r} at 0 V to match Spectre's gmin fallback."
                )
                made_progress = True
            else:
                break
        pending = next_pending

    added_nodes = set()
    for src in voltage_sources:
        for node in (src.node_pos, src.node_neg):
            if node == ground or node in added_nodes:
                continue
            sim.add_source(node, resolved_by_node[node])
            added_nodes.add(node)

    return warn


def _add_spectre_source(sim: Simulator, src: SpectreSource,
                        ground: str) -> List[str]:
    """Convert a SpectreSource to a simulator waveform and add it.

    Returns a (possibly empty) list of warning strings for degenerate cases.
    """
    return _add_spectre_sources(sim, [src], ground)


# ---------------------------------------------------------------------------
# Engineering number formatting
# ---------------------------------------------------------------------------

_ENG_PREFIXES = [
    (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
    (1, ''), (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n'),
    (1e-12, 'p'), (1e-15, 'f'),
]


def _eng_format(val: float, unit: str = '') -> str:
    """Format a number in engineering notation with unit."""
    if val == 0:
        return f"0 {unit}".strip()

    abs_val = abs(val)
    for scale, prefix in _ENG_PREFIXES:
        if abs_val >= scale * 0.999:
            scaled = val / scale
            if scaled == int(scaled):
                return f"{int(scaled)} {prefix}{unit}".strip()
            return f"{scaled:.3g} {prefix}{unit}".strip()

    return f"{val:.3g} {unit}".strip()


# ---------------------------------------------------------------------------
# Streamlined log output
# ---------------------------------------------------------------------------

class _Logger:
    """Write to both a file and optionally stdout."""

    def __init__(self, log_file: Optional[TextIO] = None, quiet: bool = False):
        self.log_file = log_file
        self.quiet = quiet

    def write(self, msg: str = '') -> None:
        if not self.quiet:
            print(msg)
        if self.log_file:
            self.log_file.write(msg + '\n')

    def flush(self) -> None:
        if self.log_file:
            self.log_file.flush()


# ---------------------------------------------------------------------------
# Bus detection and derived signals
# ---------------------------------------------------------------------------

def _find_buses(result: SimResult) -> Dict[str, Dict[int, str]]:
    """Find bus-like signal groups (name_N, name_N-1, ..., name_0).

    Returns {prefix: {index: signal_name}} for contiguous buses starting at 0.
    """
    groups: Dict[str, Dict[int, str]] = {}
    for name in result.signals:
        m = _re.match(r'^(.+?)_(\d+)$', name)
        if m:
            prefix, idx = m.group(1), int(m.group(2))
            groups.setdefault(prefix, {})[idx] = name

    buses: Dict[str, Dict[int, str]] = {}
    for prefix, bits in groups.items():
        indices = sorted(bits.keys())
        if len(indices) < 2:
            continue
        if indices[0] != 0 or indices != list(range(len(indices))):
            continue
        buses[prefix] = bits
    return buses


def _derive_bus_signals(result: SimResult) -> Dict[str, np.ndarray]:
    """Compute combined integer-valued code signals for detected buses."""
    derived: Dict[str, np.ndarray] = {}
    for prefix, bits in _find_buses(result).items():
        indices = sorted(bits.keys())
        vdd = max(float(np.max(result.signals[bits[idx]])) for idx in indices)
        if vdd == 0:
            continue
        combined = np.zeros_like(result.time)
        for idx in indices:
            combined += (result.signals[bits[idx]] / vdd) * (2 ** idx)
        derived[f'{prefix}_code'] = combined
    return derived


def _dedupe_signal_names(names: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _normalize_trace_signal_name(name: str) -> str:
    text = name.strip()
    vm = _re.match(r"(?i)^v\(\s*([^)]+)\s*\)$", text)
    if vm:
        text = vm.group(1).strip()
    return text


def _parse_required_trace_signals(simopt: Dict[str, object]) -> List[str]:
    """Return an optional harness-provided sparse trace contract.

    EVAS normally follows Spectre `save` statements, or records every node when
    no save list exists.  The benchmark harness can set this contract when the
    checker only needs a small observable subset; this keeps CLI/default EVAS
    behavior unchanged while allowing paper speed runs to avoid unnecessary
    trace arrays and CSV columns.
    """
    value = simopt.get("evas_required_trace_signals")
    if value is None:
        value = os.environ.get("EVAS_REQUIRED_TRACE_SIGNALS", "")
    if isinstance(value, (list, tuple, set)):
        raw_names = [str(item) for item in value]
    else:
        raw_names = [part for part in _re.split(r"[\s,;]+", str(value)) if part]
    normalized = [
        _normalize_trace_signal_name(name)
        for name in raw_names
    ]
    return _dedupe_signal_names([
        name
        for name in normalized
        if name and name.lower() != "time"
    ])


def _trace_nodes_for_signals(required_signals: List[str], all_nodes: set) -> List[str]:
    if not required_signals:
        return []
    lower_to_node = {str(node).lower(): node for node in all_nodes}
    nodes: List[str] = []
    for signal in required_signals:
        if signal in all_nodes:
            nodes.append(signal)
            continue
        node = lower_to_node.get(signal.lower())
        if node is not None:
            nodes.append(node)
    return _dedupe_signal_names(nodes)


def _trace_output_signals_for_request(required_signals: List[str], available_signals: set) -> List[str]:
    lower_to_signal = {str(signal).lower(): signal for signal in available_signals}
    selected: List[str] = []
    for signal in required_signals:
        if signal in available_signals:
            selected.append(signal)
            continue
        actual = lower_to_signal.get(signal.lower())
        if actual is not None:
            selected.append(actual)
    return _dedupe_signal_names(selected)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def _fmt_value(v: float, fmt: str) -> str:
    """Format a single value according to format string ('6e', '4f', 'd', etc.)."""
    if fmt == 'd':
        return str(int(round(v)))
    if fmt.endswith('f') or fmt.endswith('F'):
        try:
            n = int(fmt[:-1])
        except ValueError:
            n = 6
        return f"{v:.{n}f}"
    try:
        n = int(fmt.rstrip('eE'))
    except ValueError:
        n = 6
    return f"{v:.{n}e}"


def _csv_numpy_format(fmt: str) -> str:
    if fmt == 'd':
        return '%d'
    if fmt.endswith('f') or fmt.endswith('F'):
        try:
            n = int(fmt[:-1])
        except ValueError:
            n = 6
        return f'%.{n}f'
    try:
        n = int(fmt.rstrip('eE'))
    except ValueError:
        n = 6
    return f'%.{n}e'


def _write_csv_python(csv_path: Path, result: SimResult, valid_signals: List[str],
                      signal_arrays: List[np.ndarray], signal_formats: List[str]) -> None:
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['time'] + valid_signals)
        for i, t_value in enumerate(result.time):
            row = [f"{t_value:.12e}"]
            for values, fmt in zip(signal_arrays, signal_formats):
                row.append(_fmt_value(values[i], fmt))
            writer.writerow(row)


def _write_csv(csv_path: Path, result: SimResult, save_signals: List[str],
               save_formats: Dict[str, str] = None) -> None:
    """Write simulation results to CSV file."""
    if save_formats is None:
        save_formats = {}
    signal_names = save_signals if save_signals else sorted(result.signals.keys())
    valid_signals = [
        s for s in signal_names
        if s.lower() != "time" and s in result.signals
    ]
    signal_arrays = [result.signals[s] for s in valid_signals]
    signal_formats = [
        save_formats.get(sig, 'd' if sig.endswith('_code') else '17e')
        for sig in valid_signals
    ]

    if os.environ.get("EVAS_CSV_WRITER", "").strip().lower() == "python":
        _write_csv_python(csv_path, result, valid_signals, signal_arrays, signal_formats)
        return

    columns = [result.time]
    for values, fmt in zip(signal_arrays, signal_formats):
        if fmt == 'd':
            columns.append(np.rint(values))
        else:
            columns.append(values)
    matrix = np.column_stack(columns)
    formats = ['%.12e'] + [_csv_numpy_format(fmt) for fmt in signal_formats]
    header = ','.join(['time'] + valid_signals)
    np.savetxt(
        csv_path,
        matrix,
        delimiter=',',
        fmt=formats,
        header=header,
        comments='',
        encoding='utf-8',
    )


def _log_ahdllint_diagnostics(
    scs_path: Path,
    log: "_Logger",
    *,
    min_transition: float,
    strict_spectre: bool = False,
) -> tuple[int, bool, tuple[str, ...]]:
    """Run EVAS lint during simulation and write diagnostics to the run log."""
    from evas.compiler.linter import has_compat_errors, lint_file

    diagnostics = lint_file(
        scs_path,
        min_transition=min_transition,
        strict_spectre=strict_spectre,
    )
    log.write("")
    if strict_spectre:
        log.write("Spectre strict lint diagnostics:")
    else:
        log.write("AHDL lint diagnostics:")
    if not diagnostics:
        log.write("    No EVAS lint diagnostics.")
        return 0, False, ()
    for diagnostic in diagnostics:
        log.write(f"    {diagnostic.format_text()}")
    return (
        len(diagnostics),
        has_compat_errors(diagnostics),
        tuple(diagnostic.format_text() for diagnostic in diagnostics),
    )


# ---------------------------------------------------------------------------
# Collect all nodes from the netlist
# ---------------------------------------------------------------------------

def _collect_nodes(netlist: SpectreNetlist) -> set:
    """Gather all non-ground nodes from sources and instances."""
    nodes = set()
    for src in netlist.sources:
        nodes.add(src.node_pos)
        nodes.add(src.node_neg)
    for inst in netlist.instances:
        nodes.update(inst.nodes)
    nodes.discard(netlist.ground)
    return nodes


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _build_spectre_compile_context(
    scs_path: Path,
    *,
    netlist: Optional[SpectreNetlist] = None,
    log: Optional[_Logger] = None,
    ahdllint: bool = False,
    ahdllint_min_transition: float = 1e-12,
    spectre_strict: bool = False,
    static_branch_fastpath: Optional[bool] = None,
    state_local_fastpath: Optional[bool] = None,
    require_rust_lowering: bool = True,
) -> SpectreCompileResult:
    diagnostics: List[SpectreCompileDiagnostic] = []
    errors = 0
    warnings = 0
    failure_stage: Optional[str] = None

    def add_error(stage: str, message: str) -> None:
        nonlocal errors, failure_stage
        if failure_stage is None:
            failure_stage = stage
        diagnostics.append(SpectreCompileDiagnostic(stage=stage, message=message))
        errors += 1

    def finish(
        *,
        ok: bool,
        stage: Optional[str] = None,
        simulator: Optional[Simulator] = None,
        models_by_name: Optional[Dict[str, Tuple[object, object]]] = None,
        all_nodes: Optional[set] = None,
        record_nodes: Optional[set] = None,
        required_trace_signals: Optional[List[str]] = None,
        explicit_save_signals: Optional[List[str]] = None,
        instance_counts: Optional[Dict[str, int]] = None,
        rust_report: Optional[RustSimCompileReport] = None,
    ) -> SpectreCompileResult:
        return SpectreCompileResult(
            ok=ok,
            stage=stage or ("ok" if ok else failure_stage or "compile"),
            diagnostics=tuple(diagnostics),
            warnings=warnings,
            errors=errors,
            netlist=netlist,
            simulator=simulator,
            models_by_name=models_by_name or {},
            all_nodes=all_nodes or set(),
            record_nodes=record_nodes or set(),
            required_trace_signals=required_trace_signals or [],
            explicit_save_signals=explicit_save_signals or [],
            instance_counts=instance_counts or {},
            rust_report=rust_report,
        )

    if netlist is None:
        try:
            netlist = parse_spectre(str(scs_path))
        except Exception as e:
            message = f"Failed to parse {scs_path.name}: {e}"
            if log is not None:
                log.write(f"ERROR: {message}")
            add_error("parse", message)
            return finish(ok=False, stage="parse")

    if has_transistors(netlist):
        message = "Netlist contains transistor-level devices."
        if log is not None:
            log.write("ERROR: Netlist contains transistor-level devices.")
            log.write("       EVAS only supports behavioral Verilog-A models.")
        add_error("unsupported_netlist", message)
        return finish(ok=False, stage="unsupported_netlist")

    simopt = netlist.simulator_options or {}
    ahdllint_enabled = (
        ahdllint
        or _simopt_bool(simopt, 'ahdllint', False)
        or _simopt_bool(simopt, 'evas_ahdllint', False)
    )
    spectre_strict_enabled = (
        spectre_strict
        or _simopt_bool(simopt, 'spectre_strict', False)
        or _simopt_bool(simopt, 'evas_spectre_strict', False)
        or _env_enabled("EVAS_SPECTRE_STRICT")
    )
    if spectre_strict_enabled:
        netlist_diagnostics = strict_spectre_netlist_diagnostics(str(scs_path))
        if netlist_diagnostics:
            if log is not None:
                log.write("Spectre strict netlist diagnostics:")
                for diagnostic in netlist_diagnostics:
                    log.write(f"    {diagnostic}")
            for diagnostic in netlist_diagnostics:
                add_error("strict_netlist", str(diagnostic))
            if log is not None:
                log.write("ERROR: Spectre strict netlist validation rejected this input.")
            return finish(ok=False, stage="strict_netlist")
    if ahdllint_enabled or spectre_strict_enabled:
        lint_count, lint_has_errors, lint_messages = _log_ahdllint_diagnostics(
            scs_path,
            log if log is not None else _Logger(quiet=True),
            min_transition=ahdllint_min_transition,
            strict_spectre=spectre_strict_enabled,
        )
        warnings += lint_count
        if spectre_strict_enabled and lint_has_errors:
            for message in lint_messages:
                diagnostics.append(
                    SpectreCompileDiagnostic(
                        stage="lint",
                        message=message,
                        severity="warning",
                    )
                )
            add_error("lint", "Spectre strict lint rejected this input.")
            if log is not None:
                log.write("ERROR: Spectre strict lint rejected this input.")
            return finish(ok=False, stage="lint")

    if static_branch_fastpath is None:
        static_branch_fastpath = _simopt_bool(
            simopt,
            'evas_static_branch_fastpath',
            False,
        ) or _env_enabled("EVAS_STATIC_BRANCH_FASTPATH")
    if state_local_fastpath is None:
        state_local_fastpath = _simopt_bool(
            simopt,
            'evas_state_local_fastpath',
            False,
        ) or _simopt_bool(
            simopt,
            'evas_indexed_state_fastpath',
            False,
        ) or _env_enabled("EVAS_STATE_LOCAL_FASTPATH")
        indexed_state_fastpath_env = os.environ.get(
            "EVAS_INDEXED_STATE_FASTPATH",
            "",
        ).strip().lower()
        if indexed_state_fastpath_env:
            state_local_fastpath = indexed_state_fastpath_env in {
                "1", "true", "yes", "on", "enabled"
            }

    models_by_name: Dict[str, Tuple[object, object]] = {}
    for inc in netlist.ahdl_includes:
        p = Path(inc.path)
        scs_dir = Path(netlist.source_dir)
        va_path = p.resolve() if p.is_absolute() else (scs_dir / p).resolve()
        if not va_path.is_file():
            if log is not None:
                log.write(f"ERROR: Cannot find VA file: {inc.path!r}")
            add_error(
                "ahdl_include",
                f"Cannot find VA file: {inc.path!r}; resolved to: {va_path}",
            )
            continue

        try:
            compiled_modules = _compile_va_all(
                str(va_path),
                static_branch_fastpath_codegen=static_branch_fastpath,
                indexed_state_fastpath_codegen=state_local_fastpath,
            )
        except SpectreReservedIdentifierError as e:
            source = va_path.read_text(encoding="utf-8", errors="replace")
            lines = _format_spectre_reserved_identifier_error(source, e)
            if log is not None:
                for line in lines:
                    log.write(line)
            add_error("verilog_a_compile", "\n".join(lines))
            continue
        except Exception as e:
            message = f"Failed to compile Verilog-A file {va_path.name}: {e}"
            if log is not None:
                log.write(f"ERROR: {message}")
            add_error("verilog_a_compile", message)
            continue
        for cls, module in compiled_modules:
            if module.name in models_by_name:
                message = f"Duplicate Verilog-A module {module.name!r}"
                if log is not None:
                    log.write(f"ERROR: {message}")
                add_error("model_registry", message)
                continue
            models_by_name[module.name] = (cls, module)
            if log is not None:
                log.write(f"Compiled Verilog-A module: {module.name}")
            for w in module.warnings:
                if log is not None:
                    log.write(f"WARNING ({module.name}): {w}")
                warnings += 1

    for _mname, (_cls, _module) in models_by_name.items():
        _cls._module_registry = models_by_name

    if errors > 0:
        return finish(ok=False, models_by_name=models_by_name)

    sim = Simulator()
    try:
        src_warnings = _add_spectre_sources(sim, netlist.sources, netlist.ground)
    except ValueError as e:
        message = f"Invalid source topology: {e}"
        if log is not None:
            log.write(f"ERROR: {message}")
        add_error("source_build", message)
        src_warnings = []
    for w in src_warnings:
        if log is not None:
            log.write(f"WARNING: {w}")
        warnings += 1

    if errors > 0:
        return finish(ok=False, simulator=sim, models_by_name=models_by_name)

    instance_counts: Dict[str, int] = {}
    source_nodes = _source_constrained_nodes(netlist)
    ideal_voltage_nodes = _ideal_voltage_driven_nodes(netlist, models_by_name)
    for inst in netlist.instances:
        if inst.model_name.lower() == "resistor":
            try:
                resistance = _validate_inert_resistor_load(
                    inst,
                    netlist.ground,
                    ideal_voltage_nodes,
                )
            except ValueError as e:
                message = f"Unsupported Spectre primitive {inst.name}: {e}"
                if log is not None:
                    log.write(f"ERROR: {message}")
                add_error("instance_bind", message)
                continue
            if log is not None:
                log.write(
                    f"WARNING: Treating grounded resistor load {inst.name} "
                    f"(r={resistance:g}) as non-loading for ideal behavioral voltages."
                )
            warnings += 1
            instance_counts["resistor(load)"] = (
                instance_counts.get("resistor(load)", 0) + 1
            )
            continue
        if inst.model_name not in models_by_name:
            message = (
                f"Model {inst.model_name} not found "
                f"(available: {list(models_by_name.keys())})"
            )
            if log is not None:
                log.write(f"ERROR: {message}")
            add_error("instance_bind", message)
            continue

        cls, module = models_by_name[inst.model_name]
        try:
            model = cls()
            _validate_instance_arity(inst, module)
            unknown_overrides = _validate_instance_parameter_overrides(
                inst,
                module,
                models_by_name=models_by_name,
            )
            node_map = _build_node_map(inst, module)
            _validate_supply_drive_conflicts(inst, module, node_map, source_nodes)
        except ValueError as e:
            message = f"Spectre-incompatible instance {inst.name}: {e}"
            if log is not None:
                log.write(f"ERROR: {message}")
            add_error("instance_bind", message)
            continue
        for override in unknown_overrides:
            message = (
                f"Spectre-tolerant instance parameter override {override!r} "
                f"on {inst.name} of {module.name}"
            )
            diagnostics.append(
                SpectreCompileDiagnostic(
                    stage="instance_bind",
                    message=message,
                    severity="warning",
                )
            )
            warnings += 1
        model.node_map = node_map
        ignored_param_names = {str(name).lower() for name in unknown_overrides}
        applied_params = {
            k: v
            for k, v in inst.params.items()
            if str(k).lower() not in ignored_param_names
        }
        model._mfactor_value = float(applied_params.get("m", 1.0))
        model._given_params = {str(k).lower() for k in applied_params}
        lower_to_model_key = {k.lower(): k for k in model.params}
        param_types = {p.name.lower(): p.param_type for p in module.parameters}
        for k, v in applied_params.items():
            model_key = lower_to_model_key.get(k.lower(), k)
            if param_types.get(model_key.lower()) == va_ast.ParamType.INTEGER:
                v = int(float(v))
            elif param_types.get(model_key.lower()) == va_ast.ParamType.STRING and isinstance(v, str):
                if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
                    v = v[1:-1]
            model.params[model_key] = v
        model._refresh_child_param_overrides(refresh_parameter_defaults=True)
        sim.add_model(model)
        instance_counts[inst.model_name] = instance_counts.get(inst.model_name, 0) + 1

    all_nodes = _collect_nodes(netlist)
    if log is not None:
        log.write("")
        log.write("Circuit inventory:")
        log.write(f"{'nodes':>20s} {len(all_nodes)}")
        for mname, cnt in instance_counts.items():
            log.write(f"{mname:>20s} {cnt}")
        log.write(f"{'vsource':>20s} {len(netlist.sources)}")

    if errors > 0:
        return finish(
            ok=False,
            simulator=sim,
            models_by_name=models_by_name,
            all_nodes=all_nodes,
            instance_counts=instance_counts,
        )

    required_trace_signals = _parse_required_trace_signals(simopt)
    required_trace_nodes = _trace_nodes_for_signals(required_trace_signals, all_nodes)
    explicit_save_signals = list(netlist.save_signals)
    if required_trace_nodes and explicit_save_signals:
        record_nodes = set(explicit_save_signals) | set(required_trace_nodes)
    elif required_trace_nodes:
        record_nodes = set(required_trace_nodes)
    else:
        record_nodes = set(explicit_save_signals) if explicit_save_signals else all_nodes
    if record_nodes:
        sim.record(*sorted(record_nodes))
    if required_trace_signals and log is not None:
        record_node_lower = {node.lower() for node in required_trace_nodes}
        missing_trace = [
            signal
            for signal in required_trace_signals
            if signal not in required_trace_nodes
            and signal.lower() not in record_node_lower
        ]
        log.write("Trace counters:")
        log.write(f"    required_trace_signal_count = {len(required_trace_signals)}")
        log.write(f"    required_trace_record_node_count = {len(required_trace_nodes)}")
        log.write(f"    required_trace_missing_node_count = {len(missing_trace)}")

    try:
        rust_report = build_source_record_rust_program(
            sources=sim.sources,
            current_sources=sim.current_sources,
            recorded_signals=sim.recorded_signals.keys(),
            models=sim.models,
        )
    except Exception as exc:
        reason = f"Rust lowering raised {type(exc).__name__}: {exc}"
        if log is not None:
            log.write(f"ERROR: {reason}")
        rust_report = RustSimCompileReport(
            program=None,
            supported=False,
            reasons=(reason,),
        )
    if require_rust_lowering and (
        not rust_report.supported or rust_report.program is None
    ):
        for reason in rust_report.reasons:
            if log is not None:
                log.write(f"ERROR [rust_lowering]: {reason}")
            add_error("rust_lowering", str(reason))
        return finish(
            ok=False,
            stage="rust_lowering",
            simulator=sim,
            models_by_name=models_by_name,
            all_nodes=all_nodes,
            record_nodes=record_nodes,
            required_trace_signals=required_trace_signals,
            explicit_save_signals=explicit_save_signals,
            instance_counts=instance_counts,
            rust_report=rust_report,
        )

    return finish(
        ok=True,
        stage="ok",
        simulator=sim,
        models_by_name=models_by_name,
        all_nodes=all_nodes,
        record_nodes=record_nodes,
        required_trace_signals=required_trace_signals,
        explicit_save_signals=explicit_save_signals,
        instance_counts=instance_counts,
        rust_report=rust_report,
    )


def compile_spectre_netlist(
    scs_file: str,
    *,
    ahdllint: bool = False,
    ahdllint_min_transition: float = 1e-12,
    spectre_strict: bool = False,
) -> SpectreCompileResult:
    """Production-aligned compile-only gate for a Spectre netlist.

    The gate builds the same pre-run simulator context as ``evas_simulate`` and
    lowers it into the Rust source/record program, but never calls
    ``Simulator.run`` or advances transient time.
    """
    scs_path = Path(scs_file).resolve()
    try:
        netlist = parse_spectre(str(scs_path))
    except Exception:
        netlist = None
    python_features = (
        _netlist_python_compatibility_features(netlist)
        if netlist is not None
        else ()
    )
    return _build_spectre_compile_context(
        scs_path,
        netlist=netlist,
        ahdllint=ahdllint,
        ahdllint_min_transition=ahdllint_min_transition,
        spectre_strict=spectre_strict,
        require_rust_lowering=not python_features,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evas_simulate(scs_file: str, log_path: Optional[str] = None,
                output_dir: str = './output',
                strobe_log_path: Optional[str] = None,
                ahdllint: bool = False,
                ahdllint_min_transition: float = 1e-12,
                spectre_strict: bool = False,
                _developer_engine: Optional[str] = None) -> bool:
    """Run an EVAS .scs netlist. Returns True on success.

    Args:
        scs_file:        Path to the .scs netlist file.
        log_path:        Optional path for the simulation log. If None, log goes to stdout.
        output_dir:      Directory for output files (CSV, strobe log).
        strobe_log_path: Path for $strobe/$display output. Defaults to <output_dir>/strobe.txt.
        ahdllint:        Run EVAS AHDL-style lint before compiling models.
        ahdllint_min_transition:
                         Minimum transition rise/fall time for lint warnings.
        spectre_strict:  Reject EVAS extension syntax outside strict standalone
                         Spectre Verilog-A before compiling models.
        _developer_engine:
                         Private parity-test hook. Production callers must use
                         the fail-closed evas-rust engine.
    """
    scs_path = Path(scs_file).resolve()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_file = open(log_path, 'w', encoding='utf-8') if log_path else None
    log = _Logger(log_file, quiet=(log_path is not None))
    errors = 0
    warnings = 0

    t_total_start = time.time()

    # Banner
    now = datetime.now()
    timestamp = now.strftime("%I:%M:%S %p, %a %b %d, %Y").lstrip('0')
    log.write("EVAS -- Event-driven Verilog-A Simulator")
    log.write(f"Version {VERSION} -- {now.strftime('%b %Y')}")
    log.write("")
    log.write(f"Simulating `{scs_path.name}' at {timestamp}.")
    log.write("Command line:")
    log.write(f"    {' '.join(sys.argv)}")
    log.write("")

    # 1. Parse netlist
    log.write(f"Reading file: {scs_path.name}")
    try:
        netlist = parse_spectre(str(scs_path))
    except Exception as e:
        log.write(f"ERROR: Failed to parse {scs_path.name}: {e}")
        if log_file:
            log_file.close()
        return False

    if has_transistors(netlist):
        log.write("ERROR: Netlist contains transistor-level devices.")
        log.write("       EVAS only supports behavioral Verilog-A models.")
        if log_file:
            log_file.close()
        return False

    simopt = netlist.simulator_options or {}
    try:
        developer_engine = (
            _developer_engine
            if _developer_engine is not None
            else _DEVELOPER_ENGINE_OVERRIDE
        )
        evas_engine = _configured_evas_engine(
            simopt,
            developer_engine=developer_engine,
        )
    except ValueError as exc:
        log.write(f"ERROR: Invalid EVAS engine selection: {exc}")
        if log_file:
            log_file.close()
        return False

    requested_engine = evas_engine
    python_features = _netlist_python_compatibility_features(netlist)
    if evas_engine == RUST_EVAS_ENGINE and python_features:
        evas_engine = PYTHON_EVAS_ENGINE
        log.write(
            "Compatibility engine route: "
            f"{RUST_EVAS_ENGINE} -> {PYTHON_EVAS_ENGINE} "
            f"for {', '.join(python_features)}"
        )
        log.write("")

    identity = collect_build_identity()
    identity["engine"] = evas_engine
    identity["engine_requested"] = requested_engine
    if python_features and requested_engine == RUST_EVAS_ENGINE:
        identity["engine_fallback_kind"] = "syntax_feature_gate"
        identity["engine_fallback_features"] = list(python_features)
    write_build_identity(out_dir / "evas_identity.json", identity)
    log.write("Build identity:")
    log.write(f"    package_version = {identity['package_version']}")
    log.write(f"    engine = {evas_engine}")
    log.write(f"    rust_core_version = {identity['rust_core_version'] or 'unknown'}")
    log.write(
        "    rust_core_abi_version = "
        f"{identity['rust_core_abi_version'] if identity['rust_core_abi_version'] is not None else 'unknown'}"
    )
    log.write(f"    build_revision = {identity['build_revision'] or 'unknown'}")
    log.write(f"    rust_core_present = {str(identity['rust_core_present']).lower()}")
    log.write(f"    rust_core_loadable = {str(identity['rust_core_loadable']).lower()}")
    log.write("")
    if evas_engine == RUST_EVAS_ENGINE and not identity["rust_core_loadable"]:
        diagnostic = identity.get("rust_core_error", "unknown Rust core load error")
        log.write(
            "ERROR: EVAS Rust core is required and could not be loaded: "
            f"{diagnostic}"
        )
        log.write("ERROR: EVAS does not fall back to the Python simulation engine.")
        if log_file:
            log_file.close()
        return False

    static_branch_fastpath = _simopt_bool(
        simopt,
        'evas_static_branch_fastpath',
        False,
    ) or _env_enabled("EVAS_STATIC_BRANCH_FASTPATH")
    indexed_state_storage_requested = _simopt_bool(
        simopt,
        'evas_indexed_state_storage',
        False,
    ) or _env_enabled("EVAS_INDEXED_STATE_STORAGE")
    state_local_fastpath = _simopt_bool(
        simopt,
        'evas_state_local_fastpath',
        False,
    ) or _simopt_bool(
        simopt,
        'evas_indexed_state_fastpath',
        False,
    ) or _env_enabled("EVAS_STATE_LOCAL_FASTPATH")
    indexed_state_fastpath_env = os.environ.get(
        "EVAS_INDEXED_STATE_FASTPATH",
        "",
    ).strip().lower()
    if indexed_state_fastpath_env:
        state_local_fastpath = indexed_state_fastpath_env in {
            "1", "true", "yes", "on", "enabled"
        }

    compile_result = _build_spectre_compile_context(
        scs_path,
        netlist=netlist,
        log=log,
        ahdllint=ahdllint,
        ahdllint_min_transition=ahdllint_min_transition,
        spectre_strict=spectre_strict,
        static_branch_fastpath=static_branch_fastpath,
        state_local_fastpath=state_local_fastpath,
        require_rust_lowering=(evas_engine == RUST_EVAS_ENGINE),
    )
    errors += compile_result.errors
    warnings += compile_result.warnings
    if not compile_result.ok:
        log.write(f"\nevas completes with {errors} errors, {warnings} warnings.")
        if log_file:
            log_file.close()
        return False

    netlist = compile_result.netlist
    sim = compile_result.simulator
    all_nodes = compile_result.all_nodes
    record_nodes = compile_result.record_nodes
    required_trace_signals = compile_result.required_trace_signals
    explicit_save_signals = compile_result.explicit_save_signals
    if netlist is None or sim is None:
        log.write("ERROR: Internal compile context did not produce a simulator.")
        log.write(f"evas completes with {errors + 1} errors, {warnings} warnings.")
        if log_file:
            log_file.close()
        return False

    # 6. Run simulation
    if netlist.tran is None:
        log.write("ERROR: No transient analysis found")
        if log_file:
            log_file.close()
        return False

    tstop = netlist.tran.stop
    tstep = netlist.tran.step
    reltol = float(simopt.get('reltol', 1e-3))
    vabstol = float(simopt.get('vabstol', 1e-6))
    iabstol = float(simopt.get('iabstol', 1e-12))
    maxstep_opt = simopt.get('maxstep', None)
    if maxstep_opt is not None:
        try:
            tstep = min(float(tstep), float(maxstep_opt))
        except Exception:
            pass

    refine_factor = netlist.tran.refine_factor
    refine_steps = netlist.tran.refine_steps
    errpreset = str(netlist.tran.__dict__.get('errpreset', simopt.get('errpreset', ''))).lower()
    if errpreset == 'conservative':
        refine_factor = max(refine_factor, 32)
        refine_steps = max(refine_steps, 16)
    elif errpreset == 'liberal':
        refine_factor = min(refine_factor, 8)
        refine_steps = min(refine_steps, 4)
    # Spectre-compatible "accepted" cross event timing is an explicit opt-in
    # experiment (2026-04 closure decision: exact/analytic stays the default
    # and benchmark flows must not enable tolerance-compatible behavior).
    # The lateness model is the measured Spectre law (cross-lateness DOE,
    # 2026-06-12):  delta = factor * 0.5 * reltol * |V_cross| / |slope|,
    # with factor=1.0 reproducing Spectre's observed behavior.
    # Example: simulatorOptions options evas_cross_acceptance_slack_factor=1.0
    try:
        cross_acceptance_user_factor = max(
            0.0, float(simopt.get('evas_cross_acceptance_slack_factor', 0.0) or 0.0)
        )
    except (TypeError, ValueError):
        cross_acceptance_user_factor = 0.0

    evas_profile = str(simopt.get('evas_profile', '')).lower()
    refine_factor, refine_steps, reltol, applied_profile = _apply_evas_profile(
        evas_profile, refine_factor, refine_steps, reltol
    )
    skip_source_error_control = _simopt_bool(
        simopt,
        'evas_skip_source_error_control',
        False,
    )
    profile_sections = _simopt_bool(
        simopt,
        'evas_profile_sections',
        False,
    ) or os.environ.get("EVAS_PROFILE_SECTIONS", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    profile_model_eval = _simopt_bool(
        simopt,
        'evas_profile_model_eval',
        False,
    ) or os.environ.get("EVAS_PROFILE_MODEL_EVAL", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    profile_model_io = _simopt_bool(
        simopt,
        'evas_profile_model_io',
        False,
    ) or os.environ.get("EVAS_PROFILE_MODEL_IO", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    indexed_parity = _simopt_bool(
        simopt,
        'evas_indexed_parity',
        False,
    ) or os.environ.get("EVAS_INDEXED_PARITY", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    indexed_snapshot_profile = _simopt_bool(
        simopt,
        'evas_indexed_snapshot_profile',
        False,
    ) or os.environ.get("EVAS_INDEXED_SNAPSHOT_PROFILE", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    indexed_arrays = _simopt_bool(
        simopt,
        'evas_indexed_arrays',
        False,
    ) or os.environ.get("EVAS_INDEXED_ARRAYS", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    indexed_state_storage = indexed_state_storage_requested
    static_lifecycle_fastpath_env = (
        os.environ.get("EVAS_STATIC_LIFECYCLE_FASTPATH", "").strip().lower()
    )
    static_lifecycle_fastpath = _simopt_bool(
        simopt,
        'evas_static_lifecycle_fastpath',
        True,
    )
    if static_lifecycle_fastpath_env:
        static_lifecycle_fastpath = static_lifecycle_fastpath_env in {
            "1", "true", "yes", "on", "enabled"
        }
    transition_unchanged_fastpath_env = (
        os.environ.get("EVAS_TRANSITION_UNCHANGED_FASTPATH", "").strip().lower()
    )
    transition_unchanged_fastpath = _simopt_bool(
        simopt,
        'evas_transition_unchanged_fastpath',
        False,
    )
    if transition_unchanged_fastpath_env:
        transition_unchanged_fastpath = transition_unchanged_fastpath_env in {
            "1", "true", "yes", "on", "enabled"
        }
    rust_static_eval = _simopt_bool(
        simopt,
        'evas_rust_static_eval',
        False,
    ) or os.environ.get("EVAS_RUST_STATIC_EVAL", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_static_fast_sync = _simopt_bool(
        simopt,
        'evas_rust_static_fast_sync',
        False,
    ) or os.environ.get("EVAS_RUST_STATIC_FAST_SYNC", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    if rust_static_fast_sync:
        rust_static_eval = True
    rust_transition_shadow = _simopt_bool(
        simopt,
        'evas_rust_transition_shadow',
        False,
    ) or os.environ.get("EVAS_RUST_TRANSITION_SHADOW", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_event_due_shadow = _simopt_bool(
        simopt,
        'evas_rust_event_due_shadow',
        False,
    ) or os.environ.get("EVAS_RUST_EVENT_DUE_SHADOW", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_event_write_shadow = _simopt_bool(
        simopt,
        'evas_rust_event_write_shadow',
        False,
    ) or os.environ.get("EVAS_RUST_EVENT_WRITE_SHADOW", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_event_write_production = _simopt_bool(
        simopt,
        'evas_rust_event_write_production',
        False,
    ) or os.environ.get("EVAS_RUST_EVENT_WRITE_PRODUCTION", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_timer_event = _simopt_bool(
        simopt,
        'evas_rust_timer_event',
        False,
    ) or os.environ.get("EVAS_RUST_TIMER_EVENT", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_full_model_fastpath = _simopt_bool(
        simopt,
        'evas_rust_full_model_fastpath',
        False,
    ) or os.environ.get("EVAS_RUST_FULL_MODEL_FASTPATH", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    evas_rust_engine = (
        evas_engine == RUST_EVAS_ENGINE
        or _simopt_bool(simopt, "evas2", False)
    )
    rust_full_model_required = _simopt_bool(
        simopt,
        'evas_rust_full_model_required',
        False,
    ) or os.environ.get("EVAS_RUST_FULL_MODEL_REQUIRED", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    event_trace_audit = _simopt_bool(
        simopt,
        'evas_event_trace_audit',
        False,
    ) or os.environ.get("EVAS_EVENT_TRACE_AUDIT", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    rust_required = _simopt_bool(
        simopt,
        'evas_rust_required',
        False,
    ) or os.environ.get("EVAS_RUST_REQUIRED", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    if evas_rust_engine:
        rust_full_model_fastpath = True
        rust_full_model_required = True
        rust_required = True
    indexed_arrays_effective = (
        indexed_arrays
        or rust_static_eval
        or rust_transition_shadow
    )
    indexed_plan = None
    if indexed_parity:
        indexed_plan = build_indexed_run_plan(
            sim,
            extra_nodes=sorted(all_nodes | record_nodes),
        )

    log.write("")
    log.write("*****************************************************")
    log.write(f"Transient Analysis `{netlist.tran.name}': "
              f"time = (0 s -> {_eng_format(tstop, 's')})")
    log.write("*****************************************************")
    log.write("Important parameter values:")
    log.write("    start = 0 s")
    log.write(f"    stop  = {_eng_format(tstop, 's')}")
    log.write(f"    step  = {_eng_format(tstep, 's')}")
    log.write(f"    reltol = {reltol:g}")
    log.write(f"    vabstol = {vabstol:g}")
    log.write(f"    iabstol = {iabstol:g}")
    log.write(f"    refine_factor = {refine_factor}")
    log.write(f"    refine_steps  = {refine_steps}")
    if applied_profile:
        log.write(f"    evas_profile = {applied_profile}")
    if skip_source_error_control:
        log.write("    evas_skip_source_error_control = true")
    if profile_sections:
        log.write("    evas_profile_sections = true")
    if profile_model_eval:
        log.write("    evas_profile_model_eval = true")
    if profile_model_io:
        log.write("    evas_profile_model_io = true")
    if indexed_parity:
        log.write("    evas_indexed_parity = true")
        log.write(f"    indexed_node_count = {indexed_plan.node_count}")
    if indexed_snapshot_profile:
        log.write("    evas_indexed_snapshot_profile = true")
    if indexed_arrays_effective:
        log.write("    evas_indexed_arrays = true")
    if indexed_state_storage:
        log.write("    evas_indexed_state_storage = true")
    if state_local_fastpath:
        log.write("    evas_state_local_fastpath = true")
    if static_branch_fastpath:
        log.write("    evas_static_branch_fastpath = true")
    if not static_lifecycle_fastpath:
        log.write("    evas_static_lifecycle_fastpath = false")
    if transition_unchanged_fastpath:
        log.write("    evas_transition_unchanged_fastpath = true")
    if rust_static_eval:
        log.write("    evas_rust_static_eval = true")
    if rust_static_fast_sync:
        log.write("    evas_rust_static_fast_sync = true")
    if rust_transition_shadow:
        log.write("    evas_rust_transition_shadow = true")
    if rust_event_due_shadow:
        log.write("    evas_rust_event_due_shadow = true")
    if rust_event_write_shadow:
        log.write("    evas_rust_event_write_shadow = true")
    if rust_event_write_production:
        log.write("    evas_rust_event_write_production = true")
    if rust_timer_event:
        log.write("    evas_rust_timer_event = true")
    if rust_full_model_fastpath:
        log.write("    evas_rust_full_model_fastpath = true")
    if rust_full_model_required:
        log.write("    evas_rust_full_model_required = true")
    if evas_rust_engine:
        log.write(f"    evas_engine = {RUST_EVAS_ENGINE}")
    elif evas_engine == PYTHON_EVAS_ENGINE:
        log.write(f"    evas_engine = {PYTHON_EVAS_ENGINE}")
    if event_trace_audit:
        log.write("    evas_event_trace_audit = true")
    if rust_required:
        log.write("    evas_rust_required = true")
    # Engine-level coefficient kappa: slack = kappa * |V_cross| / |slope|.
    # Folding 0.5 * reltol here keeps the FFI channel a single scalar while
    # the user-facing factor stays a multiple of the measured Spectre law.
    cross_acceptance_slack_factor = cross_acceptance_user_factor * 0.5 * reltol
    if cross_acceptance_user_factor:
        log.write(
            f"    evas_cross_acceptance_slack_factor = {cross_acceptance_user_factor:g}"
            f" (kappa = {cross_acceptance_slack_factor:g})"
        )
    log.write("")

    t_sim_start = time.time()
    try:
        result = sim.run(tstop, tstep=tstep,
                         refine_factor=refine_factor,
                         refine_steps=refine_steps,
                         reltol=reltol,
                         vabstol=vabstol,
                         record_step=tstep,
                         skip_source_error_control=skip_source_error_control,
                         profile_sections=profile_sections,
                         profile_model_eval=profile_model_eval,
                         profile_model_io=profile_model_io,
                         indexed_snapshot_profile=indexed_snapshot_profile,
                         indexed_arrays=indexed_arrays_effective,
                         indexed_state_storage=indexed_state_storage,
                         static_branch_fastpath=static_branch_fastpath,
                         static_lifecycle_fastpath=static_lifecycle_fastpath,
                         transition_unchanged_fastpath=transition_unchanged_fastpath,
                         rust_static_eval=rust_static_eval,
                         rust_static_fast_sync=rust_static_fast_sync,
                         rust_transition_shadow=rust_transition_shadow,
                         rust_event_due_shadow=rust_event_due_shadow,
                         rust_timer_event=rust_timer_event,
                         rust_event_write_shadow=rust_event_write_shadow,
                         rust_event_write_production=rust_event_write_production,
                         rust_full_model_fastpath=rust_full_model_fastpath,
                         rust_full_model_required=rust_full_model_required,
                         event_trace_audit=event_trace_audit,
                         cross_acceptance_slack_factor=cross_acceptance_slack_factor,
                         rust_required=rust_required)
    except RuntimeError as exc:
        log.write(f"ERROR: EVAS Rust simulation failed: {exc}")
        log.write("ERROR: EVAS does not fall back to the Python simulation engine.")
        log.write(f"evas completes with {errors + 1} errors, {warnings} warnings.")
        if log_file:
            log_file.close()
        return False

    for pct in range(10, 101, 10):
        t_at = tstop * pct / 100.0
        log.write(f"    tran: time = {_eng_format(t_at, 's'):12s} ({pct:3d} %)")

    sim_elapsed_ms = (time.time() - t_sim_start) * 1000
    n_steps = len(result.time) - 1
    log.write(f"Number of accepted tran steps = {n_steps}")
    if getattr(sim, "_perf_stats", None):
        log.write("Performance counters:")
        for key, value in sorted(sim._perf_stats.items()):
            log.write(f"    {key} = {value}")
    model_perf_lines = []
    for idx, model in enumerate(sim.models):
        perf = getattr(model, "_perf_stats", None)
        if not perf:
            continue
        model_name = getattr(model, "__class__", type(model)).__name__
        model_perf_lines.append(f"    model[{idx}] {model_name}:")
        for key, value in sorted(perf.items()):
            model_perf_lines.append(f"        {key} = {value}")
    if model_perf_lines:
        log.write("Model event counters:")
        for line in model_perf_lines:
            log.write(line)
    if getattr(sim, "_profile_times", None):
        log.write("Section timing counters:")
        for key, value in sorted(sim._profile_times.items()):
            log.write(f"    {key} = {value:.6f} s")
    if getattr(sim, "_model_profile_stats", None):
        log.write("Model timing counters:")
        for model_key, stats in sorted(sim._model_profile_stats.items()):
            log.write(f"    {model_key}:")
            for key, value in sorted(stats.items()):
                if key.endswith("_s"):
                    log.write(f"        {key} = {value:.6f} s")
                else:
                    log.write(f"        {key} = {int(value)}")
    if getattr(sim, "_model_io_profile_stats", None):
        log.write("Model IO counters:")
        for key, value in sorted(sim._model_io_profile_stats.items()):
            log.write(f"    {key} = {value}")
    if getattr(sim, "_indexed_snapshot_stats", None):
        log.write("Indexed snapshot profile:")
        for key, value in sorted(sim._indexed_snapshot_stats.items()):
            log.write(f"    {key} = {value}")
    if getattr(sim, "_indexed_array_stats", None):
        log.write("Indexed array profile:")
        for key, value in sorted(sim._indexed_array_stats.items()):
            log.write(f"    {key} = {value}")
    if getattr(sim, "_indexed_model_io_stats", None):
        log.write("Indexed model IO plan:")
        for key, value in sorted(sim._indexed_model_io_stats.items()):
            log.write(f"    {key} = {value}")
    if getattr(sim, "_indexed_voltage_probe_stats", None):
        log.write("Indexed voltage read probe:")
        for key, value in sorted(sim._indexed_voltage_probe_stats.items()):
            log.write(f"    {key} = {value}")
    if getattr(sim, "_indexed_voltage_read_stats", None):
        log.write("Indexed voltage array reads:")
        for key, value in sorted(sim._indexed_voltage_read_stats.items()):
            log.write(f"    {key} = {value}")

    # Signal range summary
    log.write("")
    log.write("Maximum value achieved for any signal of each quantity:")
    max_v = 0.0
    max_v_name = ''
    for name, data in result.signals.items():
        peak = float(np.max(np.abs(data)))
        if peak > max_v:
            max_v = peak
            max_v_name = name
    if max_v_name:
        log.write(f"    V: V({max_v_name}) = {_eng_format(max_v, 'V')}")

    log.write("")
    log.write(f"Tran analysis time: CPU = {sim_elapsed_ms:.1f} ms, "
              f"elapsed = {sim_elapsed_ms:.1f} ms.")

    # 6b. Derive combined bus signals (e.g. dout_3..dout_0 -> dout_code)
    t_derive_start = time.time()
    derived = _derive_bus_signals(result)
    derive_elapsed = time.time() - t_derive_start
    result.signals.update(derived)
    if required_trace_signals and explicit_save_signals:
        required_outputs = _trace_output_signals_for_request(
            required_trace_signals,
            set(result.signals.keys()),
        )
        save_with_derived = _dedupe_signal_names(
            explicit_save_signals + required_outputs + list(derived.keys())
        )
    elif required_trace_signals:
        save_with_derived = _trace_output_signals_for_request(
            required_trace_signals,
            set(result.signals.keys()),
        )
    else:
        save_with_derived = explicit_save_signals + list(derived.keys())

    if indexed_parity:
        report = check_indexed_trace_round_trip(
            result,
            node_index=indexed_plan.node_index if indexed_plan else None,
            signal_names=save_with_derived if save_with_derived else sorted(result.signals.keys()),
        )
        log.write("Indexed parity check:")
        log.write(f"    {report.summary()}")
        if report.length_mismatches:
            log.write(
                "    length_mismatches = "
                f"{', '.join(report.length_mismatches)}"
            )
        if report.missing_signals:
            log.write(
                "    missing_requested_signals = "
                f"{', '.join(report.missing_signals)}"
            )
        if not report.passed:
            log.write("ERROR: Indexed parity check failed")
            errors += 1

    # 7. Write CSV
    csv_path = out_dir / 'tran.csv'
    t_csv_start = time.time()
    _write_csv(csv_path, result, save_with_derived, netlist.save_formats)
    csv_elapsed = time.time() - t_csv_start

    signal_names = save_with_derived if save_with_derived else sorted(result.signals.keys())
    valid_signal_names = [name for name in signal_names if name in result.signals]
    log.write("Runner timing counters:")
    log.write(f"    derive_bus_signals_s = {derive_elapsed:.6f} s")
    log.write(f"    csv_write_s = {csv_elapsed:.6f} s")
    if required_trace_signals:
        log.write("Trace counters:")
        log.write(f"    required_trace_csv_signal_count = {len(valid_signal_names)}")
    log.write("")
    log.write(f"Writing CSV: {csv_path} "
              f"(signals: {', '.join(valid_signal_names)})")

    # 8. Collect $strobe / $display output (sorted by simulation time)
    strobe_entries = []
    for model in sim.models:
        strobe_entries.extend(model._strobe_log)
    strobe_entries.sort(key=lambda x: x[0])
    strobe_lines = [msg for _, msg in strobe_entries]

    if strobe_lines:
        s_path = Path(strobe_log_path) if strobe_log_path else out_dir / 'strobe.txt'
        s_path.write_text('\n'.join(strobe_lines) + '\n', encoding='utf-8')
        log.write(f"Writing strobe log: {s_path} ({len(strobe_lines)} lines)")
        print('\n'.join(strobe_lines))

    # Final summary
    total_elapsed = time.time() - t_total_start
    log.write("")
    log.write(f"evas completes with {errors} errors, {warnings} warnings.")
    log.write(f"Total time: CPU = {total_elapsed:.1f} s, "
              f"elapsed = {total_elapsed:.1f} s.")

    if log_file:
        log_file.close()

    return errors == 0
