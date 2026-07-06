"""Static EVAS/Spectre-style lint checks for Verilog-A inputs.

The linter is intentionally separate from simulation.  It reports model
quality warnings and EVAS/Spectre compatibility errors without changing the
normal compile/simulate pass/fail path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

from evas.netlist.spectre_parser import parse_spectre
from evas.support_tiers import (
    AMS_DIGITAL,
    BEHAVIORAL_EVENT,
    CONSERVATIVE_CURRENT_KCL,
    format_support_tier_hint,
    support_boundary_message,
    support_tier_for_function,
    support_tier_for_parse_error,
    unsupported_feature_message,
)

from . import ast_nodes as va_ast
from .lexer import Token, TokenType, tokenize
from .parser import ParseError, SpectreReservedIdentifierError, parse_all
from .preprocessor import PreprocessorError, preprocess

COMPAT_ERROR = "compat-error"
STATIC_WARNING = "static-warning"
DYNAMIC_WARNING = "dynamic-warning"


@dataclass(frozen=True)
class RuleSpec:
    code: str
    severity: str
    rule: str
    spectre_ids: tuple[str, ...] = ()
    category: str = "cadence-ahdl"
    phase: str = "static-ast"
    oracle_status: str = "oracle-title-confirmed"


@dataclass
class Diagnostic:
    code: str
    severity: str
    message: str
    file: str
    line: Optional[int] = None
    column: Optional[int] = None
    module: Optional[str] = None
    rule: str = ""
    spectre_ids: List[str] = field(default_factory=list)
    support_tier: str = ""
    source: str = "evas-lint"

    def to_dict(self) -> dict:
        data = asdict(self)
        return {k: v for k, v in data.items() if v not in (None, [], "")}

    def format_text(self) -> str:
        loc = self.file
        if self.line is not None:
            loc += f":{self.line}"
            if self.column is not None:
                loc += f":{self.column}"
        module = f" [{self.module}]" if self.module else ""
        ids = f" ({', '.join(self.spectre_ids)})" if self.spectre_ids else ""
        tier = format_support_tier_hint(self.support_tier)
        return f"{loc}: {self.severity} {self.code}{ids}{tier}{module}: {self.message}"


def _rule(
    code: str,
    severity: str,
    rule: str,
    *,
    spectre_ids: tuple[str, ...] = (),
    category: str = "cadence-ahdl",
    phase: str = "static-ast",
    oracle_status: str = "oracle-title-confirmed",
) -> RuleSpec:
    return RuleSpec(
        code=code,
        severity=severity,
        rule=rule,
        spectre_ids=spectre_ids,
        category=category,
        phase=phase,
        oracle_status=oracle_status,
    )


LINT_RULE_SPECS: Dict[str, RuleSpec] = {
    spec.code: spec for spec in (
        _rule("EVAS-COMP-ENETLIST", COMPAT_ERROR, "netlist-parse", category="evas-compat", phase="netlist", oracle_status="evas-specific"),
        _rule("EVAS-COMP-EINCLUDE", COMPAT_ERROR, "ahdl-include", category="evas-compat", phase="netlist", oracle_status="evas-specific"),
        _rule("EVAS-COMP-EFILE", COMPAT_ERROR, "file-read", category="evas-compat", phase="file", oracle_status="evas-specific"),
        _rule("EVAS-COMP-EPREPROC", COMPAT_ERROR, "preprocess", category="evas-compat", phase="preprocess", oracle_status="evas-specific"),
        _rule("EVAS-COMP-E2174", COMPAT_ERROR, "reserved-identifier", spectre_ids=("VACOMP-2174",), category="spectre-compat", phase="parse"),
        _rule("EVAS-COMP-EPARSE", COMPAT_ERROR, "parse", category="evas-compat", phase="parse", oracle_status="evas-specific"),
        _rule("EVAS-COMP-WPARSE", STATIC_WARNING, "spectre-parse-compat", category="spectre-compat", phase="parse"),
        _rule("EVAS-COMP-E1519", COMPAT_ERROR, "nested-ddt", spectre_ids=("VACOMP-1519",), category="spectre-compat"),
        _rule("EVAS-COMP-E2143", COMPAT_ERROR, "conditional-analog-operator", spectre_ids=("VACOMP-2143",), category="spectre-compat"),
        _rule("EVAS-COMP-E2151", COMPAT_ERROR, "conditional-analog-operator", spectre_ids=("VACOMP-2151",), category="spectre-compat"),
        _rule("EVAS-COMP-E2154", COMPAT_ERROR, "conditional-analog-operator", spectre_ids=("VACOMP-2154",), category="spectre-compat"),
        _rule("EVAS-COMP-E2157", COMPAT_ERROR, "event-body-contribution", spectre_ids=("VACOMP-2157",), category="spectre-compat"),
        _rule("EVAS-COMP-E2446", COMPAT_ERROR, "nonconstant-discipline-range", spectre_ids=("VACOMP-2446",), category="spectre-compat", phase="static-token"),
        _rule("EVAS-COMP-EKCL", COMPAT_ERROR, "unsupported-conservative-current-kcl", category="evas-compat", oracle_status="evas-specific"),
        _rule("EVAS-COMP-ESPECTRESTRICT", COMPAT_ERROR, "strict-spectre-rejected-extension", category="spectre-compat", oracle_status="evas-specific"),
        _rule("EVAS-COMP-EUNSUPPORTED", COMPAT_ERROR, "unsupported-function", category="evas-compat", oracle_status="evas-specific"),
        _rule("EVAS-AHDL-W5003", STATIC_WARNING, "transition-missing-rise-time", spectre_ids=("AHDLLINT-5003",)),
        _rule(
            "EVAS-AHDL-W5004",
            DYNAMIC_WARNING,
            "tiny-transition-time",
            spectre_ids=(
                "AHDLLINT-5004",
                "AHDLLINT-5005",
                "AHDLLINT-8001",
                "AHDLLINT-8002",
                "AHDLLINT-8007",
            ),
            phase="static-numeric",
        ),
        _rule(
            "EVAS-AHDL-W5005",
            DYNAMIC_WARNING,
            "tiny-transition-time",
            spectre_ids=(
                "AHDLLINT-5004",
                "AHDLLINT-5005",
                "AHDLLINT-8001",
                "AHDLLINT-8002",
                "AHDLLINT-8007",
            ),
            phase="static-numeric",
        ),
        _rule("EVAS-AHDL-W5006", STATIC_WARNING, "tiny-transition-delay", spectre_ids=("AHDLLINT-5006",), phase="static-numeric"),
        _rule("EVAS-AHDL-W5007", STATIC_WARNING, "transition-continuous-input", spectre_ids=("AHDLLINT-5007", "AHDLLINT-8004")),
        _rule("EVAS-AHDL-W5008", STATIC_WARNING, "discrete-contribution-transition", spectre_ids=("AHDLLINT-5008",)),
        _rule("EVAS-AHDL-W5010", STATIC_WARNING, "conditional-potential-contribution", spectre_ids=("AHDLLINT-5010",)),
        _rule("EVAS-AHDL-W5011", STATIC_WARNING, "case-without-default", spectre_ids=("AHDLLINT-5011",)),
        _rule("EVAS-AHDL-W5012", STATIC_WARNING, "abstime-exact-equality", spectre_ids=("AHDLLINT-5012",)),
        _rule("EVAS-AHDL-W5013", STATIC_WARNING, "access-function-exact-equality", spectre_ids=("AHDLLINT-5013",)),
        _rule("EVAS-AHDL-W5014", STATIC_WARNING, "floor-ceil-contribution", spectre_ids=("AHDLLINT-5014",)),
        _rule("EVAS-AHDL-W5017", STATIC_WARNING, "electrical-gnd-name", spectre_ids=("AHDLLINT-5017",)),
        _rule("EVAS-AHDL-W5018", STATIC_WARNING, "discrete-function-argument", spectre_ids=("AHDLLINT-5018", "AHDLLINT-8011")),
        _rule("EVAS-AHDL-W5023", STATIC_WARNING, "implicit-real-to-integer-conversion", spectre_ids=("AHDLLINT-5023",)),
        _rule("EVAS-AHDL-W5024", STATIC_WARNING, "stop-finish-in-loop", spectre_ids=("AHDLLINT-5024",)),
        _rule("EVAS-AHDL-W8007", DYNAMIC_WARNING, "conditional-timer", spectre_ids=("AHDLLINT-8007",), phase="static-scheduling"),
    )
}


def _diag(
    code: str,
    message: str,
    file: str,
    *,
    line: Optional[int] = None,
    column: Optional[int] = None,
    module: Optional[str] = None,
    spectre_ids: Optional[Sequence[str]] = None,
    support_tier: str = "",
    node: object = None,
) -> Diagnostic:
    spec = LINT_RULE_SPECS[code]
    if node is not None:
        if line is None:
            line = getattr(node, "line", None)
        if column is None:
            column = getattr(node, "column", None)
    return Diagnostic(
        code=spec.code,
        severity=spec.severity,
        message=message,
        file=file,
        line=line,
        column=column,
        module=module,
        rule=spec.rule,
        spectre_ids=list(spec.spectre_ids if spectre_ids is None else spectre_ids),
        support_tier=support_tier,
    )


def lint_file(
    path: str | Path,
    *,
    min_transition: float = 1e-12,
    strict_spectre: bool = False,
) -> List[Diagnostic]:
    """Lint a Verilog-A file or a Spectre netlist with ahdl_include entries."""
    src_path = Path(path).resolve()
    if src_path.suffix.lower() == ".scs":
        return lint_spectre_netlist(
            src_path,
            min_transition=min_transition,
            strict_spectre=strict_spectre,
        )
    return lint_veriloga_file(
        src_path,
        min_transition=min_transition,
        strict_spectre=strict_spectre,
    )


def lint_spectre_netlist(
    path: str | Path,
    *,
    min_transition: float = 1e-12,
    strict_spectre: bool = False,
) -> List[Diagnostic]:
    scs_path = Path(path).resolve()
    try:
        netlist = parse_spectre(str(scs_path))
    except Exception as exc:
        return [
            _diag(
                code="EVAS-COMP-ENETLIST",
                message=f"failed to parse Spectre netlist: {exc}",
                file=str(scs_path),
            )
        ]

    diagnostics: List[Diagnostic] = []
    scs_dir = Path(netlist.source_dir)
    for inc in netlist.ahdl_includes:
        va_path = _resolve_ahdl_include(inc.path, scs_dir)
        if va_path is None:
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-EINCLUDE",
                    message=f"cannot resolve ahdl_include {inc.path!r}",
                    file=str(scs_path),
                )
            )
            continue
        diagnostics.extend(
            lint_veriloga_file(
                va_path,
                min_transition=min_transition,
                strict_spectre=strict_spectre,
            )
        )
    return diagnostics


def lint_veriloga_file(
    path: str | Path,
    *,
    min_transition: float = 1e-12,
    strict_spectre: bool = False,
) -> List[Diagnostic]:
    va_path = Path(path).resolve()
    try:
        source = va_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [
            _diag(
                code="EVAS-COMP-EFILE",
                message=f"cannot read Verilog-A file: {exc}",
                file=str(va_path),
            )
        ]
    return lint_source(
        source,
        filename=str(va_path),
        source_dir=str(va_path.parent),
        min_transition=min_transition,
        strict_spectre=strict_spectre,
    )


def lint_source(
    source: str,
    *,
    filename: str = "<string>",
    source_dir: str = ".",
    min_transition: float = 1e-12,
    strict_spectre: bool = False,
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    strict_token_diagnostics: List[Diagnostic] = []
    try:
        pp_src, _defines, _default_transition = preprocess(source, source_dir=source_dir)
        if strict_spectre:
            strict_token_diagnostics = _lint_strict_spectre_tokens(pp_src, filename)
        range_diagnostics = _lint_nonconstant_discipline_ranges(pp_src, filename)
        modules = parse_all(pp_src)
    except PreprocessorError as exc:
        return [
            _diag(
                code="EVAS-COMP-EPREPROC",
                message=str(exc),
                file=filename,
            )
        ]
    except SpectreReservedIdentifierError as exc:
        token = getattr(exc, "token", None)
        return [
            _diag(
                code="EVAS-COMP-E2174",
                message=str(exc),
                file=filename,
                line=getattr(token, "line", None),
                column=getattr(token, "col", None),
                spectre_ids=[getattr(exc, "spectre_code", "VACOMP-2174")],
            )
        ]
    except ParseError as exc:
        token = getattr(exc, "token", None)
        range_diagnostics = (
            _lint_nonconstant_discipline_ranges(pp_src, filename)
            if "pp_src" in locals()
            else []
        )
        if any(d.code == "EVAS-COMP-E2446" for d in range_diagnostics):
            return strict_token_diagnostics + range_diagnostics
        return strict_token_diagnostics + [
            _diag(
                code="EVAS-COMP-EPARSE",
                message=str(exc),
                file=filename,
                line=getattr(token, "line", None),
                column=getattr(token, "col", None),
                support_tier=support_tier_for_parse_error(str(exc)) or "",
            )
        ]

    diagnostics.extend(strict_token_diagnostics)
    diagnostics.extend(range_diagnostics)
    for module in modules:
        diagnostics.extend(
            _lint_module(
                module,
                filename,
                min_transition,
                strict_spectre=strict_spectre,
            )
        )
    return diagnostics


def has_compat_errors(diagnostics: Sequence[Diagnostic]) -> bool:
    return any(d.severity == COMPAT_ERROR for d in diagnostics)


def _resolve_ahdl_include(path_text: str, scs_dir: Path) -> Optional[Path]:
    raw = Path(path_text)
    candidates = [raw, scs_dir / raw, scs_dir / raw.name]
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def _lint_module(
    module: va_ast.Module,
    filename: str,
    min_transition: float,
    *,
    strict_spectre: bool = False,
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    for warning in module.warnings:
        diagnostics.append(
            _diag(
                code="EVAS-COMP-WPARSE",
                message=warning,
                file=filename,
                module=module.name,
            )
        )

    user_function_names = {fn.name for fn in getattr(module, "functions", [])}
    genvar_names = {
        v.name for v in getattr(module, "variables", [])
        if getattr(v, "is_genvar", False)
    }
    integer_vars = {
        v.name for v in getattr(module, "variables", [])
        if getattr(v, "var_type", None) == va_ast.ParamType.INTEGER
    }
    symbol_types = _module_symbol_types(module)
    diagnostics.extend(_lint_module_declarations(module, filename))
    if strict_spectre:
        diagnostics.extend(_lint_strict_spectre_module(module, filename))
    discrete_vars = set(integer_vars)
    if module.analog_block is not None:
        discontinuous_contribution_vars: Set[str] = set()
        for _ in range(8):
            newly_discontinuous = _assigned_from_ahdllint_5008_expr(
                module.analog_block.body,
                discontinuous_contribution_vars,
            )
            if newly_discontinuous.issubset(discontinuous_contribution_vars):
                break
            discontinuous_contribution_vars.update(newly_discontinuous)
        discrete_vars.update(_assigned_in_events(module.analog_block.body))
        for _ in range(8):
            newly_discrete = _assigned_from_discrete_expr(
                module.analog_block.body,
                discrete_vars,
            )
            if newly_discrete.issubset(discrete_vars):
                break
            discrete_vars.update(newly_discrete)
        continuous_vars: Set[str] = set()
        for _ in range(8):
            newly_continuous = _assigned_from_continuous_expr(
                module.analog_block.body,
                discrete_vars,
                continuous_vars,
            )
            newly_continuous.difference_update(discrete_vars)
            if newly_continuous.issubset(continuous_vars):
                break
            continuous_vars.update(newly_continuous)
        _lint_statement(
            module.analog_block.body,
            diagnostics,
            filename,
            module.name,
            min_transition,
            discrete_vars,
            continuous_vars,
            user_function_names,
            genvar_names,
            symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=0,
            in_event=False,
            loop_depth=0,
        )

    for function in getattr(module, "functions", []):
        _lint_statement(
            function.body,
            diagnostics,
            filename,
            module.name,
            min_transition,
            discrete_vars,
            set(),
            user_function_names,
            genvar_names,
            symbol_types,
            set(),
            conditional_depth=0,
            in_event=False,
            loop_depth=0,
        )
    for task in getattr(module, "tasks", []):
        _lint_statement(
            task.body,
            diagnostics,
            filename,
            module.name,
            min_transition,
            discrete_vars,
            set(),
            user_function_names,
            genvar_names,
            symbol_types,
            set(),
            conditional_depth=0,
            in_event=False,
            loop_depth=0,
        )
    return diagnostics


def _module_symbol_types(module: va_ast.Module) -> Dict[str, va_ast.ParamType]:
    symbol_types: Dict[str, va_ast.ParamType] = {}
    for param in getattr(module, "parameters", []):
        symbol_types[param.name] = param.param_type
    for var in getattr(module, "variables", []):
        symbol_types[var.name] = var.var_type
    for port in getattr(module, "port_decls", []):
        discipline = port.discipline.lower()
        if discipline == "logic":
            symbol_types[port.name] = va_ast.ParamType.INTEGER
        elif discipline == "wreal":
            symbol_types[port.name] = va_ast.ParamType.REAL
    return symbol_types


def _lint_module_declarations(
    module: va_ast.Module,
    filename: str,
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    for port in getattr(module, "port_decls", []):
        if (
            port.name.lower() == "gnd"
            and port.discipline.lower() in {"electrical", "voltage", "current"}
        ):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5017",
                    message=(
                        '"gnd" is declared as an electrical node rather than a '
                        "ground reference; Cadence AHDL lint treats this as a "
                        "portability risk"
                    ),
                    file=filename,
                    module=module.name,
                    node=port,
                )
            )
    return diagnostics


_STRICT_TOKEN_REJECTIONS = {
    TokenType.CONNECTMODULE: (
        "connectmodule",
        AMS_DIGITAL,
        "standalone Spectre Verilog-A rejects connectmodule artifacts",
    ),
    TokenType.LOGIC: (
        "logic",
        AMS_DIGITAL,
        "logic is an AMS/digital extension, not strict standalone Verilog-A",
    ),
    TokenType.WREAL: (
        "wreal",
        AMS_DIGITAL,
        "wreal is an AMS/digital extension, not strict standalone Verilog-A",
    ),
    TokenType.ASSIGN_KW: (
        "continuous assign",
        AMS_DIGITAL,
        "continuous assign belongs to the EVAS AMS bridge subset",
    ),
    TokenType.ALWAYS: (
        "always block",
        AMS_DIGITAL,
        "edge-sensitive always blocks require the EVAS AMS bridge subset",
    ),
    TokenType.TASK: (
        "task/endtask",
        BEHAVIORAL_EVENT,
        "current standalone Spectre rejects user task declarations in this flow",
    ),
    TokenType.DO: (
        "do while",
        BEHAVIORAL_EVENT,
        "do-while is accepted by EVAS extension mode but rejected in strict mode",
    ),
}

_STRICT_IDENT_REJECTIONS = {
    "generate": (
        "generate/genvar static elaboration",
        AMS_DIGITAL,
        "generate is accepted by EVAS extension mode but rejected in strict mode",
    ),
    "specify": (
        "specify/specparam",
        AMS_DIGITAL,
        "specify/specparam timing blocks are outside standalone Verilog-A",
    ),
    "connectrules": (
        "connectrules",
        AMS_DIGITAL,
        "connectrules require a full AMS connect-rule environment",
    ),
}

_STRICT_VERSION_GATED_RANDOM = {
    "$rdist_chi_square",
    "$rdist_t",
}

_STRICT_SEEDED_RANDOM_PARITY = {
    "$rdist_erlang",
    "$rdist_exponential",
    "$rdist_normal",
    "$rdist_poisson",
}


def _strict_spectre_diag(
    feature: str,
    tier: str,
    detail: str,
    file: str,
    *,
    module: Optional[str] = None,
    node: object = None,
    line: Optional[int] = None,
    column: Optional[int] = None,
) -> Diagnostic:
    return _diag(
        code="EVAS-COMP-ESPECTRESTRICT",
        message=(
            f"strict Spectre mode rejects {feature}: {detail}; "
            "use default EVAS extension mode for this construct"
        ),
        file=file,
        module=module,
        support_tier=tier,
        node=node,
        line=line,
        column=column,
    )


def _lint_strict_spectre_tokens(source: str, filename: str) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    try:
        tokens = tokenize(source)
    except Exception:
        return diagnostics
    for token in tokens:
        if token.type == TokenType.EOF:
            break
        spec = _STRICT_TOKEN_REJECTIONS.get(token.type)
        if spec is None and token.type == TokenType.IDENT:
            spec = _STRICT_IDENT_REJECTIONS.get(token.value.lower())
        if spec is None:
            continue
        feature, tier, detail = spec
        diagnostics.append(
            _strict_spectre_diag(
                feature,
                tier,
                detail,
                filename,
                line=token.line,
                column=token.col,
            )
        )
    return diagnostics


def _lint_strict_spectre_module(
    module: va_ast.Module,
    filename: str,
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    constant_index_names = {param.name for param in getattr(module, "parameters", [])}
    constant_index_names.update(
        var.name for var in getattr(module, "variables", [])
        if getattr(var, "is_genvar", False)
    )
    scalar_integer_names = {
        param.name for param in getattr(module, "parameters", [])
        if getattr(param, "param_type", None) == va_ast.ParamType.INTEGER
    }
    scalar_integer_names.update(
        var.name for var in getattr(module, "variables", [])
        if (
            getattr(var, "var_type", None) == va_ast.ParamType.INTEGER
            and not getattr(var, "is_array", False)
        )
    )

    for function in getattr(module, "functions", []):
        if _stmt_has_function_call(function.body, function.name):
            diagnostics.append(
                _strict_spectre_diag(
                    "recursive function",
                    BEHAVIORAL_EVENT,
                    "the current strict Spectre benchmark flow rejects recursion",
                    filename,
                    module=module.name,
                    node=function,
                )
            )
        _lint_strict_spectre_statement(
            function.body,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )

    for task in getattr(module, "tasks", []):
        _lint_strict_spectre_statement(
            task.body,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )

    for assignment in getattr(module, "continuous_assigns", []):
        _lint_strict_spectre_expr(
            assignment.target,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_expr(
            assignment.value,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )

    if module.analog_block is not None:
        _lint_strict_spectre_statement(
            module.analog_block.body,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )
    for always in getattr(module, "always_blocks", []):
        _lint_strict_spectre_event(
            always.event,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            always.body,
            diagnostics,
            filename,
            module.name,
            constant_index_names,
            scalar_integer_names,
        )
    return diagnostics


def _lint_strict_spectre_statement(
    stmt: va_ast.Statement,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    constant_index_names: Set[str],
    scalar_integer_names: Set[str],
) -> None:
    if stmt is None:
        return
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            _lint_strict_spectre_statement(
                child, diagnostics, filename, module, constant_index_names,
                scalar_integer_names,
            )
        return
    if isinstance(stmt, va_ast.Contribution):
        _lint_strict_spectre_branch(
            stmt.branch, diagnostics, filename, module, constant_index_names,
        )
        _lint_strict_spectre_expr(
            stmt.expr, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.Assignment):
        _lint_strict_spectre_expr(
            stmt.target, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_expr(
            stmt.value, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.EventStatement):
        _lint_strict_spectre_event(
            stmt.event, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.body, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.IfStatement):
        _lint_strict_spectre_expr(
            stmt.cond, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.then_body, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.else_body, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.ForStatement):
        _lint_strict_spectre_statement(
            stmt.init, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_expr(
            stmt.cond, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.update, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.body, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.WhileStatement):
        _lint_strict_spectre_expr(
            stmt.cond, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        _lint_strict_spectre_statement(
            stmt.body, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        return
    if isinstance(stmt, va_ast.CaseStatement):
        _lint_strict_spectre_expr(
            stmt.expr, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
        for item in stmt.items:
            for value in item.values:
                _lint_strict_spectre_expr(
                    value,
                    diagnostics,
                    filename,
                    module,
                    constant_index_names,
                    scalar_integer_names,
                )
            _lint_strict_spectre_statement(
                item.body, diagnostics, filename, module, constant_index_names,
                scalar_integer_names,
            )
        return
    if isinstance(stmt, va_ast.SystemTask):
        for arg in stmt.args:
            _lint_strict_spectre_expr(
                arg, diagnostics, filename, module, constant_index_names,
                scalar_integer_names,
            )
        return
    if isinstance(stmt, va_ast.TaskCall):
        for arg in stmt.args:
            _lint_strict_spectre_expr(
                arg, diagnostics, filename, module, constant_index_names,
                scalar_integer_names,
            )


def _lint_strict_spectre_event(
    event: va_ast.EventExpr | va_ast.CombinedEvent,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    constant_index_names: Set[str],
    scalar_integer_names: Set[str],
) -> None:
    if isinstance(event, va_ast.CombinedEvent):
        for child in event.events:
            _lint_strict_spectre_event(
                child, diagnostics, filename, module, constant_index_names,
                scalar_integer_names,
            )
        return
    for expr in event.args:
        _lint_strict_spectre_expr(
            expr, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )
    _lint_strict_spectre_expr(
        event.time_tol_expr, diagnostics, filename, module, constant_index_names,
        scalar_integer_names,
    )
    _lint_strict_spectre_expr(
        event.expr_tol_expr, diagnostics, filename, module, constant_index_names,
        scalar_integer_names,
    )


def _lint_strict_spectre_expr(
    expr: Optional[va_ast.Expr],
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    constant_index_names: Set[str],
    scalar_integer_names: Set[str],
) -> None:
    if expr is None:
        return
    if isinstance(expr, va_ast.BranchAccess):
        _lint_strict_spectre_branch(
            expr, diagnostics, filename, module, constant_index_names,
        )
    if isinstance(expr, va_ast.ArrayAccess) and expr.name in scalar_integer_names:
        diagnostics.append(
            _strict_spectre_diag(
                "integer bit-select",
                BEHAVIORAL_EVENT,
                "Spectre-compatible mode does not certify EVAS integer "
                "bit-select semantics",
                filename,
                module=module,
                node=expr,
            )
        )
    if isinstance(expr, va_ast.PartSelect) and expr.name in scalar_integer_names:
        diagnostics.append(
            _strict_spectre_diag(
                "integer part-select",
                BEHAVIORAL_EVENT,
                "Spectre leaves this integer part-select value unassigned in "
                "the current parity case",
                filename,
                module=module,
                node=expr,
            )
        )
    if (
        isinstance(expr, va_ast.ConcatExpr)
        and _strict_concat_uses_integer_select(expr, scalar_integer_names)
    ):
        diagnostics.append(
            _strict_spectre_diag(
                "integer select concatenation",
                BEHAVIORAL_EVENT,
                "Spectre integer select/concatenation semantics diverge from "
                "EVAS extension-mode evaluation",
                filename,
                module=module,
                node=expr,
            )
        )
    if isinstance(expr, va_ast.FunctionCall):
        name = expr.name.lower()
        if name in _STRICT_SEEDED_RANDOM_PARITY:
            diagnostics.append(
                _strict_spectre_diag(
                    f"{expr.name}()",
                    BEHAVIORAL_EVENT,
                    "seeded Spectre PRNG sequence parity is not certified for "
                    "this distribution",
                    filename,
                    module=module,
                    node=expr,
                )
            )
        if name in _STRICT_VERSION_GATED_RANDOM:
            diagnostics.append(
                _strict_spectre_diag(
                    f"{expr.name}()",
                    BEHAVIORAL_EVENT,
                    "this random distribution is version-gated out of the "
                    "current standalone Spectre compatibility target",
                    filename,
                    module=module,
                    node=expr,
                )
            )
    for child in _expr_children(expr):
        _lint_strict_spectre_expr(
            child, diagnostics, filename, module, constant_index_names,
            scalar_integer_names,
        )


def _strict_concat_uses_integer_select(
    expr: va_ast.Expr,
    scalar_integer_names: Set[str],
) -> bool:
    if isinstance(expr, va_ast.ArrayAccess):
        return expr.name in scalar_integer_names
    if isinstance(expr, va_ast.PartSelect):
        return expr.name in scalar_integer_names
    return any(
        _strict_concat_uses_integer_select(child, scalar_integer_names)
        for child in _expr_children(expr)
    )


def _lint_strict_spectre_branch(
    branch: va_ast.BranchAccess,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    constant_index_names: Set[str],
) -> None:
    index_exprs = (
        branch.node1_index,
        branch.node2_index,
        branch.node1_index2,
        branch.node2_index2,
    )
    if not any(index is not None for index in index_exprs):
        return
    if all(
        index is None or _strict_index_is_static(index, constant_index_names)
        for index in index_exprs
    ):
        return
    diagnostics.append(
        _strict_spectre_diag(
            "runtime electrical-node indexing",
            BEHAVIORAL_EVENT,
            "standalone Spectre accepts only constant or statically elaborated "
            "electrical indexes in this compatibility mode",
            filename,
            module=module,
            node=branch,
        )
    )


def _strict_index_is_static(
    expr: va_ast.Expr,
    constant_index_names: Set[str],
) -> bool:
    if isinstance(expr, va_ast.NumberLiteral):
        return True
    if isinstance(expr, va_ast.Identifier):
        return expr.name in constant_index_names
    if isinstance(expr, va_ast.UnaryExpr):
        return expr.op in {"+", "-"} and _strict_index_is_static(
            expr.operand,
            constant_index_names,
        )
    if isinstance(expr, va_ast.BinaryExpr):
        return expr.op in {"+", "-", "*", "/", "%", "**"} and (
            _strict_index_is_static(expr.left, constant_index_names)
            and _strict_index_is_static(expr.right, constant_index_names)
        )
    return False


def _stmt_has_function_call(stmt: va_ast.Statement, name: str) -> bool:
    target = name.lower()
    if stmt is None:
        return False
    if isinstance(stmt, va_ast.Block):
        return any(_stmt_has_function_call(child, name) for child in stmt.statements)
    if isinstance(stmt, va_ast.Contribution):
        return _expr_has_call(stmt.expr, target)
    if isinstance(stmt, va_ast.Assignment):
        return _expr_has_call(stmt.target, target) or _expr_has_call(stmt.value, target)
    if isinstance(stmt, va_ast.EventStatement):
        return (
            _event_has_function_call(stmt.event, target)
            or _stmt_has_function_call(stmt.body, target)
        )
    if isinstance(stmt, va_ast.IfStatement):
        return (
            _expr_has_call(stmt.cond, target)
            or _stmt_has_function_call(stmt.then_body, target)
            or _stmt_has_function_call(stmt.else_body, target)
        )
    if isinstance(stmt, va_ast.ForStatement):
        return (
            _stmt_has_function_call(stmt.init, target)
            or _expr_has_call(stmt.cond, target)
            or _stmt_has_function_call(stmt.update, target)
            or _stmt_has_function_call(stmt.body, target)
        )
    if isinstance(stmt, va_ast.WhileStatement):
        return _expr_has_call(stmt.cond, target) or _stmt_has_function_call(
            stmt.body,
            target,
        )
    if isinstance(stmt, va_ast.CaseStatement):
        return _expr_has_call(stmt.expr, target) or any(
            any(_expr_has_call(value, target) for value in item.values)
            or _stmt_has_function_call(item.body, target)
            for item in stmt.items
        )
    if isinstance(stmt, va_ast.SystemTask):
        return any(_expr_has_call(arg, target) for arg in stmt.args)
    if isinstance(stmt, va_ast.TaskCall):
        return any(_expr_has_call(arg, target) for arg in stmt.args)
    return False


def _event_has_function_call(
    event: va_ast.EventExpr | va_ast.CombinedEvent,
    name: str,
) -> bool:
    if isinstance(event, va_ast.CombinedEvent):
        return any(_event_has_function_call(child, name) for child in event.events)
    return (
        any(_expr_has_call(expr, name) for expr in event.args)
        or _expr_has_call(event.time_tol_expr, name)
        or _expr_has_call(event.expr_tol_expr, name)
    )


def _lint_statement(
    stmt: va_ast.Statement,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    min_transition: float,
    discrete_vars: Set[str],
    continuous_vars: Set[str],
    user_function_names: Set[str],
    genvar_names: Set[str],
    symbol_types: Dict[str, va_ast.ParamType],
    discontinuous_contribution_vars: Set[str],
    *,
    conditional_depth: int,
    in_event: bool,
    loop_depth: int,
) -> None:
    if stmt is None:
        return
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            _lint_statement(
                child, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, genvar_names,
                symbol_types,
                discontinuous_contribution_vars,
                conditional_depth=conditional_depth, in_event=in_event,
                loop_depth=loop_depth,
            )
        return

    if isinstance(stmt, va_ast.Contribution):
        if stmt.branch.access_type.upper() == "I":
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-EKCL",
                    message=support_boundary_message(
                        "current contribution I(...) <+ ...",
                        CONSERVATIVE_CURRENT_KCL,
                        "EVAS has limited branch-current bookkeeping helpers, "
                        "but does not certify KCL/MNA topology solving",
                    ),
                    file=filename,
                    module=module,
                    support_tier=CONSERVATIVE_CURRENT_KCL,
                    node=stmt,
                )
            )
        if in_event:
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-E2157",
                    message=(
                        "contribution statement is embedded in an analog event "
                        "body; Spectre VACOMP rejects event-local contributions"
                    ),
                    file=filename,
                    module=module,
                    node=stmt,
                )
            )
        if conditional_depth > 0:
            if stmt.branch.access_type.upper() == "V":
                diagnostics.append(
                    _diag(
                        code="EVAS-AHDL-W5010",
                        message=(
                            "potential contribution is switched by runtime "
                            "control flow; prefer a continuous contribution "
                            "with a smoothly updated target when possible"
                        ),
                        file=filename,
                        module=module,
                        node=stmt,
                    )
                )
            diagnostics.extend(
                _conditional_analog_operator_diagnostics(
                    stmt.expr,
                    filename,
                    module,
                )
            )
        if (
            not _expr_has_call(stmt.expr, "transition")
            and _expr_triggers_ahdllint_5008(stmt.expr, discontinuous_contribution_vars)
        ):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5008",
                    message=(
                        "discrete-valued expression directly drives an analog "
                        "contribution; use transition() on the discrete target "
                        "when a discontinuity is intended"
                    ),
                    file=filename,
                    module=module,
                    node=stmt,
                )
            )
        floor_ceil_call = _first_function_call(stmt.expr, {"floor", "$floor", "ceil", "$ceil"})
        if floor_ceil_call is not None:
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5014",
                    message=(
                        "floor()/ceil() appears on the right-hand side of an "
                        "analog contribution; this can introduce discontinuous "
                        "contribution levels"
                    ),
                    file=filename,
                    module=module,
                    node=floor_ceil_call,
                )
            )
        _lint_expr(
            stmt.expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(stmt, va_ast.Assignment):
        if conditional_depth > 0:
            diagnostics.extend(
                _conditional_analog_operator_diagnostics(
                    stmt.value,
                    filename,
                    module,
                )
            )
        _lint_assignment_type_conversion(stmt, diagnostics, filename, module, symbol_types)
        _lint_expr(
            stmt.target, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            stmt.value, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(stmt, va_ast.EventStatement):
        if _event_has_timer(stmt.event) and conditional_depth > 0:
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W8007",
                    message=(
                        "timer() event is inside runtime-controlled statement "
                        "flow; Cadence AHDL lint reports this as a scheduling risk"
                    ),
                    file=filename,
                    module=module,
                    node=stmt.event,
                )
            )
        _lint_event_expr(
            stmt.event, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        event_is_initial_step = (
            isinstance(stmt.event, va_ast.EventExpr)
            and stmt.event.event_type == va_ast.EventType.INITIAL_STEP
        )
        _lint_statement(
            stmt.body,
            diagnostics,
            filename,
            module,
            min_transition,
            discrete_vars,
            continuous_vars,
            user_function_names,
            genvar_names,
            symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth + 1,
            in_event=False if event_is_initial_step else True,
            loop_depth=loop_depth,
        )
        return

    if isinstance(stmt, va_ast.IfStatement):
        _lint_condition_expr(stmt.cond, diagnostics, filename, module)
        _lint_expr(
            stmt.cond, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_statement(
            stmt.then_body, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth + 1, in_event=in_event,
            loop_depth=loop_depth,
        )
        _lint_statement(
            stmt.else_body, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth + 1, in_event=in_event,
            loop_depth=loop_depth,
        )
        return

    if isinstance(stmt, va_ast.ForStatement):
        loop_var = _assignment_target_name(stmt.init)
        body_conditional_depth = (
            conditional_depth if loop_var in genvar_names else conditional_depth + 1
        )
        body_loop_depth = loop_depth if loop_var in genvar_names else loop_depth + 1
        _lint_statement(
            stmt.init, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth, in_event=in_event,
            loop_depth=loop_depth,
        )
        _lint_condition_expr(stmt.cond, diagnostics, filename, module)
        _lint_expr(
            stmt.cond, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_statement(
            stmt.update, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth, in_event=in_event,
            loop_depth=loop_depth,
        )
        _lint_statement(
            stmt.body, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=body_conditional_depth, in_event=in_event,
            loop_depth=body_loop_depth,
        )
        return

    if isinstance(stmt, va_ast.WhileStatement):
        _lint_condition_expr(stmt.cond, diagnostics, filename, module)
        _lint_expr(
            stmt.cond, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_statement(
            stmt.body, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
            discontinuous_contribution_vars,
            conditional_depth=conditional_depth + 1, in_event=in_event,
            loop_depth=loop_depth + 1,
        )
        return

    if isinstance(stmt, va_ast.CaseStatement):
        if not any(len(item.values) == 0 for item in stmt.items):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5011",
                    message=(
                        "case statement has no default branch; Cadence AHDL "
                        "lint recommends a default to keep behavior defined"
                    ),
                    file=filename,
                    module=module,
                    node=stmt,
                )
            )
        _lint_expr(
            stmt.expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        for item in stmt.items:
            for value in item.values:
                _lint_expr(
                    value, diagnostics, filename, module, min_transition,
                    discrete_vars, continuous_vars, user_function_names, symbol_types,
                )
            _lint_statement(
                item.body, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, genvar_names, symbol_types,
                discontinuous_contribution_vars,
                conditional_depth=conditional_depth + 1, in_event=in_event,
                loop_depth=loop_depth,
            )
        return

    if isinstance(stmt, va_ast.SystemTask):
        if loop_depth > 0 and stmt.name.lower() in {"$stop", "$finish"}:
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5024",
                    message=(
                        f"{stmt.name} appears inside a loop; Cadence AHDL lint "
                        "flags simulator-stop tasks in looping statements"
                    ),
                    file=filename,
                    module=module,
                    node=stmt,
                )
            )
        for arg in stmt.args:
            _lint_expr(
                arg, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )
        return

    if isinstance(stmt, va_ast.TaskCall):
        if stmt.name == "$indirect_branch":
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-EKCL",
                    message=support_boundary_message(
                        "indirect branch equation",
                        CONSERVATIVE_CURRENT_KCL,
                        "EVAS preserves selected syntax as a behavioral "
                        "helper, but does not certify conservative topology "
                        "solving",
                    ),
                    file=filename,
                    module=module,
                    support_tier=CONSERVATIVE_CURRENT_KCL,
                    node=stmt,
                )
            )
        for arg in stmt.args:
            _lint_expr(
                arg, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )


def _lint_event_expr(
    event: va_ast.EventExpr | va_ast.CombinedEvent,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    min_transition: float,
    discrete_vars: Set[str],
    continuous_vars: Set[str],
    user_function_names: Set[str],
    symbol_types: Dict[str, va_ast.ParamType],
) -> None:
    if isinstance(event, va_ast.CombinedEvent):
        for child in event.events:
            _lint_event_expr(
                child, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )
        return
    for expr in event.args:
        _lint_expr(
            expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
    _lint_expr(
        event.time_tol_expr, diagnostics, filename, module, min_transition,
        discrete_vars, continuous_vars, user_function_names, symbol_types,
    )
    _lint_expr(
        event.expr_tol_expr, diagnostics, filename, module, min_transition,
        discrete_vars, continuous_vars, user_function_names, symbol_types,
    )


def _lint_expr(
    expr: Optional[va_ast.Expr],
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    min_transition: float,
    discrete_vars: Set[str],
    continuous_vars: Set[str],
    user_function_names: Set[str],
    symbol_types: Dict[str, va_ast.ParamType],
) -> None:
    if expr is None:
        return

    if isinstance(expr, va_ast.BinaryExpr):
        if expr.op in {"==", "!="} and (
            _is_abstime_expr(expr.left) or _is_abstime_expr(expr.right)
        ):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5012",
                    message=(
                        "$abstime is compared for exact equality; use an event, "
                        "timer(), or a tolerance/windowed comparison instead"
                    ),
                    file=filename,
                    module=module,
                    node=expr,
                )
            )
        _lint_expr(
            expr.left, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.right, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.UnaryExpr):
        _lint_expr(
            expr.operand, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.TernaryExpr):
        _lint_condition_expr(expr.cond, diagnostics, filename, module)
        _lint_expr(
            expr.cond, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.true_expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.false_expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.ConcatExpr):
        for part in expr.parts:
            _lint_expr(
                part, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )
        return

    if isinstance(expr, va_ast.ReplicateExpr):
        _lint_expr(
            expr.count, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.expr, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.ArrayAccess):
        _lint_expr(
            expr.index, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.index2, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.PartSelect):
        _lint_expr(
            expr.msb, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.lsb, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.BranchAccess):
        if expr.access_type.upper() == "I":
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-EKCL",
                    message=support_boundary_message(
                        "current probe I(...)",
                        CONSERVATIVE_CURRENT_KCL,
                        "EVAS can expose selected branch-current bookkeeping, "
                        "but does not certify KCL/MNA current solving",
                    ),
                    file=filename,
                    module=module,
                    support_tier=CONSERVATIVE_CURRENT_KCL,
                    node=expr,
                )
            )
        _lint_expr(
            expr.node1_index, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.node2_index, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.node1_index2, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        _lint_expr(
            expr.node2_index2, diagnostics, filename, module, min_transition,
            discrete_vars, continuous_vars, user_function_names, symbol_types,
        )
        return

    if isinstance(expr, va_ast.MethodCall):
        for arg in expr.args:
            _lint_expr(
                arg, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )
        return

    if isinstance(expr, va_ast.FunctionCall):
        name = expr.name.lower()
        if name == "transition":
            _lint_transition_call(
                expr, diagnostics, filename, module, min_transition,
                continuous_vars,
            )
        elif name == "ddt" and any(_expr_has_call(arg, "ddt") for arg in expr.args):
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-E1519",
                    message="nested ddt(ddt(...)) is not a Spectre-compatible analog operator form",
                    file=filename,
                    module=module,
                    node=expr,
                )
            )
        if name == "slew" and any(
            _expr_has_integer_state_reference(arg, symbol_types) for arg in expr.args
        ):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5018",
                    message=(
                        f"{expr.name}() receives an integer/state argument; "
                        "Cadence AHDL lint flags this discrete value inside "
                        "the slew expression"
                    ),
                    file=filename,
                    module=module,
                    node=expr,
                )
            )
        if not _is_supported_function(expr.name, user_function_names):
            support_tier = support_tier_for_function(expr.name)
            diagnostics.append(
                _diag(
                    code="EVAS-COMP-EUNSUPPORTED",
                    message=unsupported_feature_message(
                        f"{expr.name}()",
                        support_tier,
                        "no EVAS behavioral implementation is registered for "
                        "this function/operator",
                    ),
                    file=filename,
                    module=module,
                    support_tier=support_tier,
                    node=expr,
                )
            )
        for arg in expr.args:
            _lint_expr(
                arg, diagnostics, filename, module, min_transition,
                discrete_vars, continuous_vars, user_function_names, symbol_types,
            )


def _lint_transition_call(
    expr: va_ast.FunctionCall,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    min_transition: float,
    continuous_vars: Set[str],
) -> None:
    if expr.args and (
        _expr_contains_branch_access(expr.args[0])
        or _expr_references_any(expr.args[0], continuous_vars)
    ):
        diagnostics.append(
            _diag(
                code="EVAS-AHDL-W5007",
                message=(
                    "transition() input depends on a continuous branch value; "
                    "Cadence AHDL lint recommends feeding transition() with a "
                    "discrete target updated by events"
                ),
                file=filename,
                module=module,
                node=expr,
            )
        )

    if len(expr.args) < 3:
        diagnostics.append(
            _diag(
                code="EVAS-AHDL-W5003",
                message=(
                    "transition() has no explicit rise time; provide an "
                    "implementation rise time rather than relying on defaults"
                ),
                file=filename,
                module=module,
                node=expr,
            )
        )

    if len(expr.args) > 1:
        delay_value = _numeric_value(expr.args[1])
        if delay_value is not None and (
            delay_value < 0.0 or 0.0 < delay_value < min_transition
        ):
            diagnostics.append(
                _diag(
                    code="EVAS-AHDL-W5006",
                    message=(
                        "transition() delay is negative or smaller than "
                        f"the lint minimum {min_transition:g}s"
                    ),
                    file=filename,
                    module=module,
                    node=expr,
                )
            )

    for idx, code in ((2, "EVAS-AHDL-W5004"), (3, "EVAS-AHDL-W5005")):
        if len(expr.args) <= idx:
            continue
        value = _numeric_value(expr.args[idx])
        if value is None:
            continue
        if value <= 0.0 or value < min_transition:
            diagnostics.append(
                _diag(
                    code=code,
                    message=(
                        "transition() rise/fall time is zero or smaller than "
                        f"the lint minimum {min_transition:g}s"
                    ),
                    file=filename,
                    module=module,
                    node=expr,
                )
            )


def _lint_condition_expr(
    expr: Optional[va_ast.Expr],
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
) -> None:
    equality_expr = _expr_branch_access_equality(expr)
    if equality_expr is not None:
        diagnostics.append(
            _diag(
                code="EVAS-AHDL-W5013",
                message=(
                    "branch access value is compared for exact equality inside "
                    "a conditional expression; use a tolerance or event-driven "
                    "threshold crossing instead"
                ),
                file=filename,
                module=module,
                node=equality_expr,
            )
        )


def _lint_assignment_type_conversion(
    stmt: va_ast.Assignment,
    diagnostics: List[Diagnostic],
    filename: str,
    module: str,
    symbol_types: Dict[str, va_ast.ParamType],
) -> None:
    target_name = _assignment_target_name(stmt)
    if target_name is None or symbol_types.get(target_name) != va_ast.ParamType.INTEGER:
        return
    compatibility = _expr_integer_compatibility(stmt.value, symbol_types)
    if compatibility is False:
        diagnostics.append(
            _diag(
                code="EVAS-AHDL-W5023",
                message=(
                    "real-valued expression is assigned to an integer target; "
                    "use an explicit conversion such as $rtoi() when precision "
                    "loss is intended"
                ),
                file=filename,
                module=module,
                node=stmt,
            )
        )


def _assigned_in_events(stmt: va_ast.Statement, in_event: bool = False) -> Set[str]:
    names: Set[str] = set()
    if stmt is None:
        return names
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            names.update(_assigned_in_events(child, in_event))
    elif isinstance(stmt, va_ast.EventStatement):
        event_is_initial_step = (
            isinstance(stmt.event, va_ast.EventExpr)
            and stmt.event.event_type == va_ast.EventType.INITIAL_STEP
        )
        names.update(_assigned_in_events(stmt.body, False if event_is_initial_step else True))
    elif isinstance(stmt, va_ast.Assignment):
        if in_event:
            target = _assignment_target_name(stmt)
            if target:
                names.add(target)
    elif isinstance(stmt, va_ast.IfStatement):
        names.update(_assigned_in_events(stmt.then_body, in_event))
        names.update(_assigned_in_events(stmt.else_body, in_event))
    elif isinstance(stmt, va_ast.ForStatement):
        names.update(_assigned_in_events(stmt.init, in_event))
        names.update(_assigned_in_events(stmt.update, in_event))
        names.update(_assigned_in_events(stmt.body, in_event))
    elif isinstance(stmt, va_ast.WhileStatement):
        names.update(_assigned_in_events(stmt.body, in_event))
    elif isinstance(stmt, va_ast.CaseStatement):
        for item in stmt.items:
            names.update(_assigned_in_events(item.body, in_event))
    return names


def _assigned_from_discrete_expr(
    stmt: va_ast.Statement,
    discrete_vars: Set[str],
) -> Set[str]:
    names: Set[str] = set()
    if stmt is None:
        return names
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            names.update(_assigned_from_discrete_expr(child, discrete_vars))
    elif isinstance(stmt, va_ast.Assignment):
        target = _assignment_target_name(stmt)
        if (
            target
            and not _expr_has_call(stmt.value, "transition")
            and _expr_has_discrete_behavior(stmt.value, discrete_vars)
        ):
            names.add(target)
    elif isinstance(stmt, va_ast.EventStatement):
        names.update(_assigned_from_discrete_expr(stmt.body, discrete_vars))
    elif isinstance(stmt, va_ast.IfStatement):
        names.update(_assigned_from_discrete_expr(stmt.then_body, discrete_vars))
        names.update(_assigned_from_discrete_expr(stmt.else_body, discrete_vars))
    elif isinstance(stmt, va_ast.ForStatement):
        names.update(_assigned_from_discrete_expr(stmt.init, discrete_vars))
        names.update(_assigned_from_discrete_expr(stmt.update, discrete_vars))
        names.update(_assigned_from_discrete_expr(stmt.body, discrete_vars))
    elif isinstance(stmt, va_ast.WhileStatement):
        names.update(_assigned_from_discrete_expr(stmt.body, discrete_vars))
    elif isinstance(stmt, va_ast.CaseStatement):
        for item in stmt.items:
            names.update(_assigned_from_discrete_expr(item.body, discrete_vars))
    return names


def _assigned_from_ahdllint_5008_expr(
    stmt: va_ast.Statement,
    discontinuous_vars: Set[str],
    *,
    in_event: bool = False,
) -> Set[str]:
    names: Set[str] = set()
    if stmt is None:
        return names
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            names.update(
                _assigned_from_ahdllint_5008_expr(
                    child,
                    discontinuous_vars,
                    in_event=in_event,
                )
            )
    elif isinstance(stmt, va_ast.EventStatement):
        event_is_initial_step = (
            isinstance(stmt.event, va_ast.EventExpr)
            and stmt.event.event_type == va_ast.EventType.INITIAL_STEP
        )
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.body,
                discontinuous_vars,
                in_event=False if event_is_initial_step else True,
            )
        )
    elif isinstance(stmt, va_ast.Assignment):
        target = _assignment_target_name(stmt)
        if (
            target
            and not in_event
            and _expr_triggers_ahdllint_5008(stmt.value, discontinuous_vars)
        ):
            names.add(target)
    elif isinstance(stmt, va_ast.IfStatement):
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.then_body,
                discontinuous_vars,
                in_event=in_event,
            )
        )
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.else_body,
                discontinuous_vars,
                in_event=in_event,
            )
        )
    elif isinstance(stmt, va_ast.ForStatement):
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.init,
                discontinuous_vars,
                in_event=in_event,
            )
        )
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.update,
                discontinuous_vars,
                in_event=in_event,
            )
        )
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.body,
                discontinuous_vars,
                in_event=in_event,
            )
        )
    elif isinstance(stmt, va_ast.WhileStatement):
        names.update(
            _assigned_from_ahdllint_5008_expr(
                stmt.body,
                discontinuous_vars,
                in_event=in_event,
            )
        )
    elif isinstance(stmt, va_ast.CaseStatement):
        for item in stmt.items:
            names.update(
                _assigned_from_ahdllint_5008_expr(
                    item.body,
                    discontinuous_vars,
                    in_event=in_event,
                )
            )
    return names


def _assigned_from_continuous_expr(
    stmt: va_ast.Statement,
    discrete_vars: Set[str],
    continuous_vars: Set[str],
    in_event: bool = False,
) -> Set[str]:
    names: Set[str] = set()
    if stmt is None:
        return names
    if isinstance(stmt, va_ast.Block):
        for child in stmt.statements:
            names.update(
                _assigned_from_continuous_expr(
                    child,
                    discrete_vars,
                    continuous_vars,
                    in_event,
                )
            )
    elif isinstance(stmt, va_ast.Assignment):
        target = _assignment_target_name(stmt)
        if (
            target
            and not in_event
            and _expr_has_continuous_behavior(
                stmt.value,
                discrete_vars,
                continuous_vars,
            )
        ):
            names.add(target)
    elif isinstance(stmt, va_ast.EventStatement):
        names.update(
            _assigned_from_continuous_expr(
                stmt.body,
                discrete_vars,
                continuous_vars,
                True,
            )
        )
    elif isinstance(stmt, va_ast.IfStatement):
        names.update(
            _assigned_from_continuous_expr(
                stmt.then_body,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
        names.update(
            _assigned_from_continuous_expr(
                stmt.else_body,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
    elif isinstance(stmt, va_ast.ForStatement):
        names.update(
            _assigned_from_continuous_expr(
                stmt.init,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
        names.update(
            _assigned_from_continuous_expr(
                stmt.update,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
        names.update(
            _assigned_from_continuous_expr(
                stmt.body,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
    elif isinstance(stmt, va_ast.WhileStatement):
        names.update(
            _assigned_from_continuous_expr(
                stmt.body,
                discrete_vars,
                continuous_vars,
                in_event,
            )
        )
    elif isinstance(stmt, va_ast.CaseStatement):
        for item in stmt.items:
            names.update(
                _assigned_from_continuous_expr(
                    item.body,
                    discrete_vars,
                    continuous_vars,
                    in_event,
                )
            )
    return names


def _assignment_target_name(assign: Optional[va_ast.Assignment]) -> Optional[str]:
    if assign is None:
        return None
    target = getattr(assign, "target", None)
    if isinstance(target, va_ast.Identifier):
        return target.name
    if isinstance(target, va_ast.ArrayAccess):
        return target.name
    return None


def _event_has_timer(event: va_ast.EventExpr | va_ast.CombinedEvent) -> bool:
    if isinstance(event, va_ast.CombinedEvent):
        return any(_event_has_timer(child) for child in event.events)
    return event.event_type == va_ast.EventType.TIMER


def _conditional_analog_operator_diagnostics(
    expr: Optional[va_ast.Expr],
    filename: str,
    module: str,
) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    for call in _iter_function_calls(expr):
        spec = _CONDITIONAL_ANALOG_OPERATOR_CODES.get(call.name.lower())
        if spec is None:
            continue
        code, spectre_id = spec
        diagnostics.append(
            _diag(
                code=code,
                message=(
                    f"{call.name}() analog operator is inside a runtime "
                    "conditional/event/loop/case statement"
                ),
                file=filename,
                module=module,
                spectre_ids=[spectre_id],
                node=call,
            )
        )
    return diagnostics


def _iter_function_calls(expr: Optional[va_ast.Expr]) -> Iterable[va_ast.FunctionCall]:
    if expr is None:
        return
    if isinstance(expr, va_ast.FunctionCall):
        yield expr
    for child in _expr_children(expr):
        yield from _iter_function_calls(child)


def _expr_has_call(expr: Optional[va_ast.Expr], call_name: str) -> bool:
    if expr is None:
        return False
    target = call_name.lower()
    if isinstance(expr, va_ast.FunctionCall):
        return expr.name.lower() == target or any(
            _expr_has_call(arg, call_name) for arg in expr.args
        )
    return any(_expr_has_call(child, call_name) for child in _expr_children(expr))


def _expr_has_any_call(expr: Optional[va_ast.Expr], call_names: Set[str]) -> bool:
    if expr is None:
        return False
    targets = {name.lower() for name in call_names}
    if isinstance(expr, va_ast.FunctionCall):
        return expr.name.lower() in targets or any(
            _expr_has_any_call(arg, targets) for arg in expr.args
        )
    return any(_expr_has_any_call(child, targets) for child in _expr_children(expr))


def _first_function_call(
    expr: Optional[va_ast.Expr],
    call_names: Set[str],
) -> Optional[va_ast.FunctionCall]:
    if expr is None:
        return None
    targets = {name.lower() for name in call_names}
    if isinstance(expr, va_ast.FunctionCall):
        if expr.name.lower() in targets:
            return expr
        for arg in expr.args:
            match = _first_function_call(arg, targets)
            if match is not None:
                return match
    for child in _expr_children(expr):
        match = _first_function_call(child, targets)
        if match is not None:
            return match
    return None


def _expr_contains_branch_access(expr: Optional[va_ast.Expr]) -> bool:
    if expr is None:
        return False
    if isinstance(expr, va_ast.BranchAccess):
        return True
    return any(_expr_contains_branch_access(child) for child in _expr_children(expr))


def _expr_references_any(expr: Optional[va_ast.Expr], names: Set[str]) -> bool:
    if expr is None or not names:
        return False
    if isinstance(expr, va_ast.Identifier):
        return expr.name in names
    if isinstance(expr, (va_ast.ArrayAccess, va_ast.PartSelect)):
        return expr.name in names or any(
            _expr_references_any(child, names) for child in _expr_children(expr)
        )
    return any(_expr_references_any(child, names) for child in _expr_children(expr))


def _expr_branch_access_equality(
    expr: Optional[va_ast.Expr],
) -> Optional[va_ast.BinaryExpr]:
    if expr is None:
        return None
    if isinstance(expr, va_ast.BinaryExpr) and expr.op in {"==", "!="}:
        if _expr_contains_branch_access(expr.left) or _expr_contains_branch_access(
            expr.right
        ):
            return expr
    for child in _expr_children(expr):
        match = _expr_branch_access_equality(child)
        if match is not None:
            return match
    return None


def _expr_triggers_ahdllint_5008(
    expr: Optional[va_ast.Expr],
    discontinuous_vars: Set[str],
) -> bool:
    if expr is None:
        return False
    if isinstance(expr, va_ast.TernaryExpr):
        return True
    if isinstance(expr, va_ast.BinaryExpr):
        if expr.op in {"==", "!=", ">", "<", ">=", "<=", "&&", "||", "&", "|", "^", "<<", ">>"}:
            return True
        return (
            _expr_triggers_ahdllint_5008(expr.left, discontinuous_vars)
            or _expr_triggers_ahdllint_5008(expr.right, discontinuous_vars)
        )
    if isinstance(expr, va_ast.UnaryExpr):
        if expr.op in {"!", "~"}:
            return True
        return _expr_triggers_ahdllint_5008(expr.operand, discontinuous_vars)
    if isinstance(expr, va_ast.Identifier):
        return expr.name in discontinuous_vars
    if isinstance(expr, va_ast.ArrayAccess):
        return expr.name in discontinuous_vars
    if isinstance(expr, va_ast.PartSelect):
        return expr.name in discontinuous_vars
    if isinstance(expr, va_ast.FunctionCall):
        name = expr.name.lower()
        if name == "transition":
            return False
        if name == "slew":
            return any(
                _expr_triggers_ahdllint_5008(arg, discontinuous_vars)
                for arg in expr.args
            )
        return False
    return False


def _expr_has_discrete_behavior(expr: Optional[va_ast.Expr], discrete_vars: Set[str]) -> bool:
    if expr is None:
        return False
    if isinstance(expr, va_ast.TernaryExpr):
        return True
    if isinstance(expr, va_ast.BinaryExpr):
        if expr.op in {"==", "!=", ">", "<", ">=", "<=", "&&", "||", "&", "|", "^", "<<", ">>"}:
            return True
        return (
            _expr_has_discrete_behavior(expr.left, discrete_vars)
            or _expr_has_discrete_behavior(expr.right, discrete_vars)
        )
    if isinstance(expr, va_ast.UnaryExpr):
        if expr.op in {"!", "~"}:
            return True
        return _expr_has_discrete_behavior(expr.operand, discrete_vars)
    if isinstance(expr, va_ast.Identifier):
        return expr.name in discrete_vars
    if isinstance(expr, va_ast.ArrayAccess):
        return expr.name in discrete_vars or any(
            _expr_has_discrete_behavior(child, discrete_vars)
            for child in _expr_children(expr)
        )
    return any(
        _expr_has_discrete_behavior(child, discrete_vars)
        for child in _expr_children(expr)
    )


def _expr_has_integer_state_reference(
    expr: Optional[va_ast.Expr],
    symbol_types: Dict[str, va_ast.ParamType],
) -> bool:
    if expr is None:
        return False
    if isinstance(expr, va_ast.Identifier):
        return symbol_types.get(expr.name) == va_ast.ParamType.INTEGER
    if isinstance(expr, (va_ast.ArrayAccess, va_ast.PartSelect)):
        return symbol_types.get(expr.name) == va_ast.ParamType.INTEGER or any(
            _expr_has_integer_state_reference(child, symbol_types)
            for child in _expr_children(expr)
        )
    return any(
        _expr_has_integer_state_reference(child, symbol_types)
        for child in _expr_children(expr)
    )


def _expr_has_continuous_behavior(
    expr: Optional[va_ast.Expr],
    discrete_vars: Set[str],
    continuous_vars: Set[str],
) -> bool:
    if expr is None or _expr_has_discrete_behavior(expr, discrete_vars):
        return False
    if isinstance(expr, va_ast.BranchAccess):
        return True
    if isinstance(expr, va_ast.Identifier):
        return expr.name in continuous_vars
    if isinstance(expr, (va_ast.ArrayAccess, va_ast.PartSelect)):
        return expr.name in continuous_vars or any(
            _expr_has_continuous_behavior(child, discrete_vars, continuous_vars)
            for child in _expr_children(expr)
        )
    return any(
        _expr_has_continuous_behavior(child, discrete_vars, continuous_vars)
        for child in _expr_children(expr)
    )


def _expr_integer_compatibility(
    expr: Optional[va_ast.Expr],
    symbol_types: Dict[str, va_ast.ParamType],
) -> Optional[bool]:
    if expr is None:
        return None
    if isinstance(expr, va_ast.NumberLiteral):
        return float(expr.value).is_integer()
    if isinstance(expr, va_ast.StringLiteral):
        return None
    if isinstance(expr, va_ast.Identifier):
        symbol_type = symbol_types.get(expr.name)
        if symbol_type is None:
            return None
        return symbol_type == va_ast.ParamType.INTEGER
    if isinstance(expr, (va_ast.ArrayAccess, va_ast.PartSelect)):
        symbol_type = symbol_types.get(expr.name)
        if symbol_type is None:
            return None
        return symbol_type == va_ast.ParamType.INTEGER
    if isinstance(expr, va_ast.BranchAccess):
        return False
    if isinstance(expr, va_ast.FunctionCall):
        name = expr.name.lower()
        if name in _INTEGER_RETURN_FUNCTIONS:
            return True
        if name in _REAL_RETURN_FUNCTIONS:
            return False
        return None
    if isinstance(expr, va_ast.MethodCall):
        return None
    if isinstance(expr, va_ast.UnaryExpr):
        if expr.op in {"!", "~"}:
            return True
        return _expr_integer_compatibility(expr.operand, symbol_types)
    if isinstance(expr, va_ast.BinaryExpr):
        if expr.op in {"==", "!=", ">", "<", ">=", "<=", "&&", "||"}:
            return True
        if expr.op in {"&", "|", "^", "<<", ">>", "%"}:
            return _merge_integer_compatibility(
                _expr_integer_compatibility(expr.left, symbol_types),
                _expr_integer_compatibility(expr.right, symbol_types),
            )
        return _merge_integer_compatibility(
            _expr_integer_compatibility(expr.left, symbol_types),
            _expr_integer_compatibility(expr.right, symbol_types),
        )
    if isinstance(expr, va_ast.TernaryExpr):
        return _merge_integer_compatibility(
            _expr_integer_compatibility(expr.true_expr, symbol_types),
            _expr_integer_compatibility(expr.false_expr, symbol_types),
        )
    if isinstance(expr, va_ast.ConcatExpr):
        return True
    if isinstance(expr, va_ast.ReplicateExpr):
        return _expr_integer_compatibility(expr.expr, symbol_types)
    return None


def _merge_integer_compatibility(
    lhs: Optional[bool],
    rhs: Optional[bool],
) -> Optional[bool]:
    if lhs is False or rhs is False:
        return False
    if lhs is True and rhs is True:
        return True
    return None


_DISCIPLINE_RANGE_TOKENS = {
    TokenType.ELECTRICAL,
    TokenType.VOLTAGE,
    TokenType.CURRENT,
}

_DIRECTION_TOKENS = {
    TokenType.INPUT,
    TokenType.OUTPUT,
    TokenType.INOUT,
}

_CONSTANT_RANGE_OPERATORS = {
    TokenType.NUMBER,
    TokenType.IDENT,
    TokenType.PLUS,
    TokenType.MINUS,
    TokenType.STAR,
    TokenType.SLASH,
    TokenType.PERCENT,
    TokenType.LPAREN,
    TokenType.RPAREN,
    TokenType.COLON,
}


def _lint_nonconstant_discipline_ranges(
    source: str,
    filename: str,
) -> List[Diagnostic]:
    tokens = tokenize(source)
    diagnostics: List[Diagnostic] = []
    emitted: set[tuple[int, int]] = set()
    for module_tokens in _iter_module_token_slices(tokens):
        parameter_names = _module_parameter_names(module_tokens)
        for statement in _iter_semicolon_statements(module_tokens):
            if not _is_discipline_declaration_statement(statement):
                continue
            for lbracket, range_tokens in _iter_bracket_ranges(statement):
                key = (lbracket.line, lbracket.col)
                if key in emitted:
                    continue
                if _range_uses_nonconstant_identifier(range_tokens, parameter_names):
                    emitted.add(key)
                    diagnostics.append(
                        _diag(
                            code="EVAS-COMP-E2446",
                            message=(
                                "discipline vector range uses a non-constant "
                                "identifier; Spectre requires numeric or "
                                "parameter constant expressions"
                            ),
                            file=filename,
                            line=lbracket.line,
                            column=lbracket.col,
                        )
                    )
    return diagnostics


def _iter_module_token_slices(tokens: Sequence[Token]) -> Iterable[Sequence[Token]]:
    idx = 0
    while idx < len(tokens):
        if tokens[idx].type not in {TokenType.MODULE, TokenType.CONNECTMODULE}:
            idx += 1
            continue
        end_type = (
            TokenType.ENDCONNECTMODULE
            if tokens[idx].type == TokenType.CONNECTMODULE
            else TokenType.ENDMODULE
        )
        start = idx
        idx += 1
        while idx < len(tokens) and tokens[idx].type != end_type:
            idx += 1
        if idx < len(tokens):
            idx += 1
        yield tokens[start:idx]


def _module_parameter_names(tokens: Sequence[Token]) -> set[str]:
    names: set[str] = set()
    idx = 0
    while idx < len(tokens):
        if tokens[idx].type != TokenType.PARAMETER:
            idx += 1
            continue
        idx += 1
        if idx < len(tokens) and tokens[idx].type in {
            TokenType.REAL,
            TokenType.INTEGER,
        }:
            idx += 1
        elif (
            idx < len(tokens)
            and tokens[idx].type == TokenType.IDENT
            and tokens[idx].value == "string"
        ):
            idx += 1
        if idx < len(tokens) and tokens[idx].type == TokenType.IDENT:
            names.add(tokens[idx].value)
        while idx < len(tokens) and tokens[idx].type != TokenType.SEMI:
            idx += 1
    return names


def _iter_semicolon_statements(tokens: Sequence[Token]) -> Iterable[Sequence[Token]]:
    start = 0
    for idx, token in enumerate(tokens):
        if token.type == TokenType.SEMI:
            yield tokens[start:idx + 1]
            start = idx + 1


def _is_discipline_declaration_statement(tokens: Sequence[Token]) -> bool:
    types = {token.type for token in tokens}
    return bool(types & _DISCIPLINE_RANGE_TOKENS)


def _iter_bracket_ranges(
    tokens: Sequence[Token],
) -> Iterable[tuple[Token, Sequence[Token]]]:
    idx = 0
    while idx < len(tokens):
        if tokens[idx].type != TokenType.LBRACKET:
            idx += 1
            continue
        start = idx
        depth = 1
        idx += 1
        while idx < len(tokens) and depth:
            if tokens[idx].type == TokenType.LBRACKET:
                depth += 1
            elif tokens[idx].type == TokenType.RBRACKET:
                depth -= 1
            idx += 1
        end = idx - 1
        if end > start:
            yield tokens[start], tokens[start + 1:end]


def _range_uses_nonconstant_identifier(
    tokens: Sequence[Token],
    parameter_names: set[str],
) -> bool:
    for token in tokens:
        if token.type not in _CONSTANT_RANGE_OPERATORS:
            return True
        if token.type == TokenType.IDENT and token.value not in parameter_names:
            return True
    return False


def _expr_children(expr: va_ast.Expr) -> Iterable[va_ast.Expr]:
    if isinstance(expr, va_ast.FunctionCall):
        yield from expr.args
    elif isinstance(expr, va_ast.MethodCall):
        yield from expr.args
    elif isinstance(expr, va_ast.BinaryExpr):
        yield expr.left
        yield expr.right
    elif isinstance(expr, va_ast.UnaryExpr):
        yield expr.operand
    elif isinstance(expr, va_ast.TernaryExpr):
        yield expr.cond
        yield expr.true_expr
        yield expr.false_expr
    elif isinstance(expr, va_ast.ConcatExpr):
        yield from expr.parts
    elif isinstance(expr, va_ast.ReplicateExpr):
        yield expr.count
        yield expr.expr
    elif isinstance(expr, va_ast.ArrayAccess):
        yield expr.index
        if expr.index2 is not None:
            yield expr.index2
    elif isinstance(expr, va_ast.PartSelect):
        yield expr.msb
        yield expr.lsb
    elif isinstance(expr, va_ast.BranchAccess):
        for child in (
            expr.node1_index,
            expr.node2_index,
            expr.node1_index2,
            expr.node2_index2,
        ):
            if child is not None:
                yield child


def _numeric_value(expr: va_ast.Expr) -> Optional[float]:
    if isinstance(expr, va_ast.NumberLiteral):
        return float(expr.value)
    if isinstance(expr, va_ast.UnaryExpr) and expr.op == "-":
        value = _numeric_value(expr.operand)
        return -value if value is not None else None
    return None


def _is_abstime_expr(expr: va_ast.Expr) -> bool:
    if isinstance(expr, va_ast.Identifier):
        return expr.name == "$abstime"
    return isinstance(expr, va_ast.FunctionCall) and expr.name == "$abstime"


_INTEGER_RETURN_FUNCTIONS = {
    "$rtoi", "floor", "$floor", "ceil", "$ceil", "$random",
}

_REAL_RETURN_FUNCTIONS = {
    "transition", "slew", "ddt", "idt", "idtmod",
    "limexp", "ln", "log", "exp", "sqrt", "abs", "pow", "min", "max",
    "sin", "cos", "tan", "tanh",
    "$ln", "$log", "$exp", "$sqrt", "$abs", "$pow", "$min", "$max",
    "$sin", "$cos", "$tan", "$tanh", "$temperature", "$vt", "$realtime",
    "white_noise", "flicker_noise", "noise_table",
    "$rdist_normal", "$rdist_exponential", "$rdist_poisson",
    "$rdist_chi_square", "$rdist_t", "$rdist_erlang",
}


_SUPPORTED_FUNCTIONS = {
    "transition", "slew", "ddt", "idt", "idtmod", "cross", "last_crossing",
    "limexp", "laplace_nd", "laplace_np", "laplace_zd", "laplace_zp",
    "zi_nd", "zi_np", "zi_zd", "zi_zp", "ln", "log", "exp", "sqrt",
    "abs", "pow", "min", "max", "sin", "cos", "tan", "tanh", "floor",
    "ceil", "$ln", "$log", "$exp", "$sqrt", "$abs", "$pow", "$min",
    "$max", "$sin", "$cos", "$tan", "$tanh", "$floor", "$ceil",
    "$temperature", "$vt", "$simparam", "$attribute", "potential", "flow",
    "$rtoi", "$param_given", "$port_connected", "$mfactor",
    "$analog_node_alias", "$analog_port_alias", "$cds_get_mc_trial_number",
    "$cds_set_rf_source_info", "$cds_violation", "$table_model",
    "$rdist_normal", "$rdist_exponential", "$rdist_poisson",
    "$rdist_chi_square", "$rdist_t", "$rdist_erlang", "$dist_normal",
    "$dist_exponential", "$dist_poisson", "$dist_chi_square", "$dist_t",
    "$dist_erlang", "$random", "$dist_uniform", "$rdist_uniform", "$fopen",
    "$fclose", "$fwrite", "$fstrobe", "$strobe", "$display", "$debug",
    "$warning", "$error", "$info", "$fscanf", "$sscanf", "$fgets", "$feof", "$fseek",
    "$ftell", "$rewind", "$swrite", "$sformat", "analysis", "ac_stim",
    "white_noise", "flicker_noise", "noise_table", "$abstime", "$realtime",
    "$bound_step", "$discontinuity",
}


def _is_supported_function(name: str, user_function_names: Set[str]) -> bool:
    return name in _SUPPORTED_FUNCTIONS or name in user_function_names


_CONDITIONAL_ANALOG_OPERATOR_CODES = {
    "transition": ("EVAS-COMP-E2143", "VACOMP-2143"),
    "slew": ("EVAS-COMP-E2151", "VACOMP-2151"),
    "idt": ("EVAS-COMP-E2154", "VACOMP-2154"),
}
