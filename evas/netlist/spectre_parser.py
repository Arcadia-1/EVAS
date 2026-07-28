"""
spectre_parser.py — Spectre .scs netlist parser for NexSim.

Parses real Cadence Spectre netlist syntax:
  simulator lang=spectre       — Accept and skip
  global 0                     — Record ground node
  parameters k=expr ...        — Global parameters with expression evaluation
  include "file" [section=X]   — Record process includes; load .va files
  ahdl_include "file.va"       — VA model include
  Vname (n+ n-) vsource ...    — DC/pulse/pwl/sin/square source
  Iname (nodes) Model k=v      — VA model instance
  tran tran stop=val ...       — Transient analysis
  save node1 node2 ...         — Signals to record
  simulatorOptions options ...  — Parse temp, skip rest
  // comment                   — C-style line comments
  * comment                    — Spectre/SPICE full-line comments
  \\ at EOL                    — Line continuation
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------

@dataclass
class AhdlInclude:
    path: str  # VA file path as written in netlist


@dataclass
class SpectreSource:
    """A voltage/current source parsed from Spectre syntax."""
    name: str
    node_pos: str
    node_neg: str
    source_type: str  # 'dc', 'pulse', 'pwl', 'sin', 'square'
    params: Dict[str, Any] = field(default_factory=dict)
    kind: str = "voltage"


@dataclass
class SpectreInstance:
    """A subcircuit/model instance."""
    name: str
    nodes: List[str]
    model_name: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpectreTran:
    """Transient analysis parameters."""
    stop: float
    step: Optional[float] = None  # maxstep or computed
    name: str = 'tran'
    refine_factor: int = 16   # step divisor after a cross event
    refine_steps: int = 8     # number of refined steps after a cross event
    errpreset: str = ''       # e.g. conservative/moderate/liberal


@dataclass
class SpectreMosfet:
    """A MOSFET device inside a subckt."""
    name: str         # M88
    nodes: List[str]  # [drain, gate, source, bulk]
    model: str        # nch_ulvt_mac
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpectreSubckt:
    """A subcircuit definition containing MOSFETs and/or instances."""
    name: str
    ports: List[str]
    mosfets: List[SpectreMosfet] = field(default_factory=list)
    instances: List[SpectreInstance] = field(default_factory=list)
    body_lines: List[str] = field(default_factory=list)  # raw lines for pass-through


@dataclass
class SpectreInclude:
    """An include statement with optional section."""
    path: str
    section: Optional[str] = None  # e.g. "TOP_TT"


@dataclass
class SpectreNetlist:
    title: str = ''
    ground: str = '0'
    parameters: Dict[str, float] = field(default_factory=dict)
    ahdl_includes: List[AhdlInclude] = field(default_factory=list)
    sources: List[SpectreSource] = field(default_factory=list)
    instances: List[SpectreInstance] = field(default_factory=list)
    subckts: List[SpectreSubckt] = field(default_factory=list)
    includes: List[SpectreInclude] = field(default_factory=list)
    tran: Optional[SpectreTran] = None
    simulator_options: Dict[str, Any] = field(default_factory=dict)
    save_signals: List[str] = field(default_factory=list)
    save_formats: Dict[str, str] = field(default_factory=dict)  # sig -> fmt e.g. '6e', '10e', 'd'
    temp: float = 27.0
    source_dir: str = ''


# ---------------------------------------------------------------------------
# SPICE suffix parser
# ---------------------------------------------------------------------------

_SUFFIXES_CASE_SENSITIVE = {
    's': 1.0,
    'T': 1e12,
    'G': 1e9,
    'M': 1e6,
    'K': 1e3,
    'k': 1e3,
    'm': 1e-3,
    'u': 1e-6,
    'U': 1e-6,
    'n': 1e-9,
    'N': 1e-9,
    'p': 1e-12,
    'P': 1e-12,
    'f': 1e-15,
    'F': 1e-15,
    'a': 1e-18,
    'A': 1e-18,
}

_SUFFIXES_CASE_INSENSITIVE = {
    'MEG': 1e6,
    'X': 1e6,
}

_SPECTRE_TIME_UNITS = {
    's': 1.0,
    'ms': 1e-3,
    'us': 1e-6,
    'ns': 1e-9,
    'ps': 1e-12,
    'fs': 1e-15,
    'as': 1e-18,
}


def _parse_suffix_number(s: str) -> Optional[float]:
    """Try to parse a number with optional SPICE suffix. Returns None if not a number."""
    s = s.strip()
    if not s:
        return None

    # Try direct float first
    try:
        return float(s)
    except ValueError:
        pass

    # Spectre accepts an explicit seconds unit after an engineering prefix,
    # for example ``18us``.  Parse these before the one-letter suffix table so
    # the trailing ``s`` is not mistaken for the entire suffix.
    for suffix, multiplier in sorted(
        _SPECTRE_TIME_UNITS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)]) * multiplier
            except ValueError:
                continue

    # Spectre engineering suffixes are case-sensitive for M/m.  Keep the
    # common case-insensitive multi-letter aliases separately so 100M means
    # 100e6 while 900m remains 0.9.
    for suffix in sorted(_SUFFIXES_CASE_SENSITIVE.keys(), key=len, reverse=True):
        if s.endswith(suffix):
            num_part = s[:-len(suffix)]
            try:
                return float(num_part) * _SUFFIXES_CASE_SENSITIVE[suffix]
            except ValueError:
                continue

    s_upper = s.upper()
    for suffix in sorted(_SUFFIXES_CASE_INSENSITIVE.keys(), key=len, reverse=True):
        if s_upper.endswith(suffix):
            num_part = s[:-len(suffix)]
            try:
                return float(num_part) * _SUFFIXES_CASE_INSENSITIVE[suffix]
            except ValueError:
                continue

    return None


# ---------------------------------------------------------------------------
# Expression evaluator (for `parameters` block)
# ---------------------------------------------------------------------------

class _ExprEvaluator:
    """Simple recursive-descent expression evaluator.

    Supports: +, -, *, /, unary minus, parentheses, variable substitution,
    and SPICE-suffix numbers.
    """

    def __init__(self, variables: Dict[str, float]):
        self.variables = variables
        self.pos = 0
        self.expr = ''

    def evaluate(self, expr: str) -> float:
        self.expr = expr.strip()
        self.pos = 0
        result = self._parse_expr()
        return result

    def _peek(self) -> Optional[str]:
        self._skip_ws()
        if self.pos < len(self.expr):
            return self.expr[self.pos]
        return None

    def _skip_ws(self):
        while self.pos < len(self.expr) and self.expr[self.pos] in ' \t':
            self.pos += 1

    def _parse_expr(self) -> float:
        """expr = term (('+' | '-') term)*"""
        left = self._parse_term()
        while True:
            op = self._peek()
            if op == '+':
                self.pos += 1
                left += self._parse_term()
            elif op == '-':
                self.pos += 1
                left -= self._parse_term()
            else:
                break
        return left

    def _parse_term(self) -> float:
        """term = unary (('*' | '/') unary)*"""
        left = self._parse_unary()
        while True:
            op = self._peek()
            if op == '*':
                self.pos += 1
                left *= self._parse_unary()
            elif op == '/':
                self.pos += 1
                right = self._parse_unary()
                left = left / right if right != 0 else 0.0
            else:
                break
        return left

    def _parse_unary(self) -> float:
        """unary = '-' unary | '+' unary | atom"""
        ch = self._peek()
        if ch == '-':
            self.pos += 1
            return -self._parse_unary()
        if ch == '+':
            self.pos += 1
            return self._parse_unary()
        return self._parse_atom()

    def _parse_atom(self) -> float:
        """atom = '(' expr ')' | number_with_suffix | variable"""
        self._skip_ws()

        # Parenthesized expression
        if self.pos < len(self.expr) and self.expr[self.pos] == '(':
            self.pos += 1
            val = self._parse_expr()
            self._skip_ws()
            if self.pos >= len(self.expr) or self.expr[self.pos] != ')':
                raise ValueError("Expected closing parenthesis")
            self.pos += 1
            return val

        # Read token: number or variable name
        # Handle scientific notation: e.g. 5e-11, 1.2E+3
        start = self.pos
        while self.pos < len(self.expr) and self.expr[self.pos] not in '+-*/() \t':
            self.pos += 1
        # Check if we stopped at +/- that's part of scientific notation (e.g. 5e-11)
        while (self.pos < len(self.expr) and
               self.pos > start and
               self.expr[self.pos] in '+-' and
               self.expr[self.pos - 1] in 'eE'):
            self.pos += 1  # consume the +/-
            # Continue reading digits/suffix
            while self.pos < len(self.expr) and self.expr[self.pos] not in '+-*/() \t':
                self.pos += 1
        token = self.expr[start:self.pos]

        if not token:
            raise ValueError("Expected expression atom")

        # Try as number with suffix
        val = _parse_suffix_number(token)
        if val is not None:
            return val

        # Try as variable
        if token in self.variables:
            return self.variables[token]

        # Unknown — try harder with case-insensitive variable lookup
        for k, v in self.variables.items():
            if k.lower() == token.lower():
                return v

        raise ValueError(f"Unknown variable or invalid number: {token!r}")


def evaluate_expr(expr: str, variables: Dict[str, float]) -> float:
    """Evaluate an expression string with variable substitution."""
    return _ExprEvaluator(variables).evaluate(expr)


def _evaluate_complete_expr(expr: str, variables: Dict[str, float]) -> float:
    """Evaluate an expression and reject trailing or incomplete syntax."""
    evaluator = _ExprEvaluator(variables)
    result = evaluator.evaluate(expr)
    evaluator._skip_ws()
    if evaluator.pos != len(evaluator.expr):
        raise ValueError(
            f"Unexpected trailing expression text: {evaluator.expr[evaluator.pos:]!r}"
        )
    return result


# ---------------------------------------------------------------------------
# Line preprocessing: strip comments, handle continuation
# ---------------------------------------------------------------------------

def _strip_line_comment(line: str) -> str:
    """Strip Spectre line comments while respecting quoted strings."""
    if line.lstrip().startswith('*'):
        return ''
    in_quote = False
    quote_char = None
    for i, ch in enumerate(line):
        if in_quote:
            if ch == quote_char:
                in_quote = False
        else:
            if ch in ('"', "'"):
                in_quote = True
                quote_char = ch
            elif ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                return line[:i].rstrip()
            elif ch == ';':
                return line[:i].rstrip()
    return line


def _validate_pwl_line_continuations(raw_lines: List[str]) -> None:
    """Reject implicit multiline PWL arrays without Spectre continuation marks.

    Spectre 21.1.0.509 ISR12 reports SFE-874 (unexpected end of line) for
    bracketed PWL data split across unmarked physical lines.
    """
    in_wave = False
    previous_backslash = False
    saw_plus_continuation = False
    saw_wave_data = False
    for line_number, raw in enumerate(raw_lines, 1):
        line = _strip_line_comment(raw.rstrip())
        stripped = line.strip()
        if not stripped:
            continue

        starts_plus = stripped.startswith('+')
        continued = stripped.endswith('\\')

        if in_wave:
            search_text = stripped[1:].lstrip() if starts_plus else stripped
            if search_text == ']':
                if saw_wave_data and not starts_plus and not previous_backslash:
                    style = (
                        "'+'-continued"
                        if saw_plus_continuation
                        else "backslash-continued"
                    )
                    raise ValueError(
                        "Spectre-incompatible PWL wave syntax at "
                        f"line {line_number}: a {style} wave requires "
                        "the closing bracket to remain on a continued line"
                    )
                in_wave = False
                previous_backslash = continued
                saw_plus_continuation = False
                saw_wave_data = False
                continue
            if not starts_plus and not previous_backslash:
                raise ValueError(
                    "Spectre-incompatible PWL wave syntax at "
                    f"line {line_number}: multiline wave=[...] requires '+' "
                    "or backslash line continuation"
                )
            if starts_plus:
                saw_plus_continuation = True
            saw_wave_data = True
            if ']' in search_text:
                in_wave = False
                saw_plus_continuation = False
                saw_wave_data = False
            previous_backslash = continued
            continue

        match = re.search(r'\bwave\s*=\s*\[', stripped, flags=re.IGNORECASE)
        if match is None:
            previous_backslash = continued
            continue
        if ']' not in stripped[match.end():]:
            in_wave = True
            previous_backslash = continued
            saw_plus_continuation = False
            saw_wave_data = bool(stripped[match.end():].strip())
            continue
        previous_backslash = continued


def _preprocess_lines(raw_lines: List[str]) -> List[str]:
    """Strip comments, handle \\ continuation and bracket blocks, return clean lines."""
    result = []
    continuation = ''
    continuation_from_backslash = False
    bracket_depth = 0

    for line_number, raw in enumerate(raw_lines, 1):
        raw_line = raw.rstrip()
        line = _strip_line_comment(raw_line)
        comment_suffix = raw_line[len(line):].lstrip()
        has_slash_comment = comment_suffix.startswith('//')
        if has_slash_comment and (
            continuation
            or bracket_depth > 0
            or line.rstrip().endswith('\\')
        ):
            raise ValueError(
                "Spectre-incompatible comment inside a continued Spectre "
                f"statement at line {line_number}"
            )
        if line.lstrip().startswith('+'):
            if continuation_from_backslash:
                raise ValueError(
                    "Spectre-incompatible continuation syntax at "
                    f"line {line_number}: cannot mix backslash and '+' "
                    "continuation markers on the same statement"
                )
            continued_text = line.lstrip()[1:].lstrip()
            if continuation or bracket_depth > 0:
                line = continued_text
            else:
                if not result:
                    raise ValueError(
                        "Spectre continuation marker '+' has no preceding "
                        f"statement at line {line_number}"
                    )
                line = f"{result.pop()} {continued_text}"

        continued = line.endswith('\\')
        if continued:
            line = line[:-1].rstrip()

        in_quote = False
        quote_char = None
        for ch in line:
            if in_quote:
                if ch == quote_char:
                    in_quote = False
                continue
            if ch in ('"', "'"):
                in_quote = True
                quote_char = ch
                continue
            if ch == '[':
                bracket_depth += 1
            elif ch == ']':
                bracket_depth -= 1

        # Handle continuation
        if continued:
            continuation += line + ' '
            continuation_from_backslash = True
            continue

        if continuation:
            line = continuation + line.lstrip()
            continuation = ''
            continuation_from_backslash = False

        if bracket_depth > 0:
            continuation = line.rstrip() + ' '
            continuation_from_backslash = False
            continue

        stripped = line.strip()
        if stripped:
            result.append(stripped)

    # Flush any trailing continuation
    if continuation:
        result.append(continuation.strip())

    return result


def strict_spectre_netlist_diagnostics(path: str) -> List[str]:
    """Return strict Spectre compatibility diagnostics for netlist syntax.

    EVAS accepts a few convenience spellings in ordinary mode.  Strict mode
    must reject spellings that Spectre itself does not accept so a local EVAS
    pass cannot hide a later Spectre parse failure.
    """
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    diagnostics: List[str] = []
    for line_number, raw_line in enumerate(raw_lines, 1):
        line = _strip_line_comment(raw_line).strip()
        if re.fullmatch(r"(?i)gnd\s*\(\s*0\s*\)", line):
            diagnostics.append(
                "EVAS-NETLIST-ESPECTRESTRICT: strict Spectre ground syntax "
                f"rejects `gnd (0)` at line {line_number}; use `global 0` "
                "to declare the Spectre ground node"
            )
    for line in _preprocess_lines(raw_lines):
        if not line.lower().startswith("save"):
            continue
        for match in re.finditer(r"(?i)\b[vi]\s*\([^)]*\)", line):
            token = match.group(0)
            diagnostics.append(
                "EVAS-NETLIST-ESPECTRESTRICT: strict Spectre save syntax "
                f"expects a raw node or branch name; found `save {token}`"
            )
    return diagnostics


# ---------------------------------------------------------------------------
# Source parameter parsing
# ---------------------------------------------------------------------------

def _parse_named_params(tokens: List[str], start: int,
                        variables: Dict[str, float]) -> Dict[str, Any]:
    """Parse key=value pairs from token list starting at index `start`."""
    params = {}
    pending_key: Optional[str] = None
    idx = start
    while idx < len(tokens):
        tok = tokens[idx]
        idx += 1

        if pending_key is not None:
            # Consume the value in forms like `foo = bar` where `bar` is token.
            if '=' in tok and not tok.startswith('='):
                raise ValueError(
                    f"Missing value for Spectre parameter {pending_key!r}"
                )
            val_str = tok
            if val_str.startswith('='):
                val_str = val_str[1:].strip()
            if val_str == "":
                # Handle `foo =` `bar` where bar comes later.
                continue
            params[pending_key] = _parse_param_value(val_str, variables)
            pending_key = None
            continue

        if '=' not in tok:
            if tok == '=':
                continue
            if tok.endswith('='):
                key = tok[:-1].strip().lower()
                if key:
                    pending_key = key
                continue
            if idx < len(tokens) and tokens[idx].startswith('='):
                pending_key = tok.strip().lower()
            continue

        key, val_str = tok.split('=', 1)
        key = key.strip().lower()
        val_str = val_str.strip()
        if not key:
            continue
        if val_str == "":
            pending_key = key
            continue
        if pending_key is None:
            params[key] = _parse_param_value(val_str, variables)

    if pending_key is not None:
        raise ValueError(
            f"Missing value for Spectre parameter {pending_key!r}"
        )
    return params


def _parse_param_value(val_str: str, variables: Dict[str, float]) -> Any:
    """Parse one parameter value token.

    Supports raw expressions and bracket-enclosed array payloads.
    """
    if val_str.startswith('['):
        return val_str[1:]
    try:
        return evaluate_expr(val_str, variables)
    except (ValueError, ZeroDivisionError):
        return val_str


def _is_inline_wave_arithmetic(tok: str) -> bool:
    """Return True for PWL wave tokens Spectre rejects as inline arithmetic.

    Spectre accepts engineering-suffix literals such as ``10n`` and exponent
    signs such as ``1e-9``, but it does not accept unparenthesized arithmetic
    tokens like ``30n+10p`` or ``21.5n-100p`` inside ``wave=[...]`` arrays.
    Those expressions must be precomputed by the testbench author.
    """
    text = tok.strip()
    if text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    break
            if depth < 0:
                break
        else:
            if depth == 0:
                return False
    for i, ch in enumerate(text):
        if ch not in "+-":
            continue
        if i == 0:
            continue
        if text[i - 1] in "eE":
            continue
        return True
    return False


def _split_pwl_wave_tokens(wave_data: str, source_name: str) -> List[str]:
    """Split a Spectre PWL wave array without breaking parenthesized expressions."""
    tokens: List[str] = []
    token_chars: List[str] = []
    depth = 0

    for ch in wave_data:
        if ch == "(":
            depth += 1
            token_chars.append(ch)
            continue
        if ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    f"Invalid PWL wave token in source {source_name}: "
                    "unmatched closing parenthesis"
                )
            token_chars.append(ch)
            continue
        if depth == 0 and (ch.isspace() or ch == ","):
            if token_chars:
                tokens.append("".join(token_chars).strip())
                token_chars = []
            continue
        token_chars.append(ch)

    if depth != 0:
        raise ValueError(
            f"Invalid PWL wave token in source {source_name}: "
            "unmatched opening parenthesis"
        )
    if token_chars:
        tokens.append("".join(token_chars).strip())
    return [tok for tok in tokens if tok]


def _parenthesize_bare_two_terminal_primitive(line: str) -> str:
    """Normalize Spectre's optional bare two-terminal primitive syntax.

    Cadence accepts both ``V1 (out 0) vsource ...`` and
    ``V1 out 0 vsource ...`` as well as the analogous resistor form.  Keeping
    normalization here lets the existing primitive parsers remain the single
    implementation.
    """
    match = re.match(
        r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+([vi]source|resistor)\b(.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if match is None:
        instance_name = line.split(maxsplit=1)[0]
        raise ValueError(
            f"Invalid bare-terminal Spectre primitive {instance_name!r}; "
            "expected `name positive_node negative_node "
            "vsource|isource|resistor ...`."
        )
    name, node_pos, node_neg, primitive, remainder = match.groups()
    return f"{name} ({node_pos} {node_neg}) {primitive}{remainder}"


def _is_bare_two_terminal_primitive_syntax(line: str) -> bool:
    """Return whether *line* starts with a supported bare primitive form.

    Looking for any ``(`` in the complete line is insufficient because legal
    source parameters, notably PWL expressions, may themselves contain
    parentheses.  Restrict the decision to the four leading source tokens.
    """
    return (
        re.match(
            r"^\s*\S+\s+[^()\s]+\s+[^()\s]+\s+"
            r"(?:[vi]source|resistor)\b",
            line,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _normalize_source_keyword_instance(line: str) -> str:
    """Normalize Spectre's source-keyword-prefixed forms.

    The non-redundant ``vsource Vclk (p n) ...`` spelling uses ``Vclk`` as the
    component name.  A redundant trailing primitive,
    ``vsource Vclk (p n) vsource ...``, is observably different in Spectre:
    the leading keyword remains the instance identity and the source terminal
    orientation is reversed.  Preserve that behavior instead of silently
    repairing generated testbenches into the intended canonical form.
    """
    match = re.match(
        r"""^\s*([vi]source)\s+
            ("[^"]*"|'[^']*'|[^()\s]+)\s+
            (\([^)]*\))(.*)$""",
        line,
        flags=re.IGNORECASE | re.VERBOSE,
    )
    if match is None:
        return line

    source_master, instance_name, nodes, remainder = match.groups()
    quoted_instance_name = (
        (instance_name.startswith('"') and instance_name.endswith('"'))
        or (instance_name.startswith("'") and instance_name.endswith("'"))
    )
    if quoted_instance_name:
        instance_name = instance_name[1:-1]

    remainder = remainder.strip()
    if remainder:
        first_tail, sep, rest = remainder.partition(" ")
        if (
            not quoted_instance_name
            and first_tail.lower() in {"vsource", "isource"}
        ):
            if first_tail.lower() != source_master.lower():
                raise ValueError(
                    f"Spectre source instance {instance_name!r} uses conflicting "
                    f"primitive names {source_master!r} and {first_tail!r}"
                )
            node_names = nodes[1:-1].split()
            if len(node_names) != 2:
                raise ValueError(
                    f"Spectre source instance {source_master!r} requires two "
                    f"terminals, got {nodes!r}"
                )
            reversed_nodes = f"({node_names[1]} {node_names[0]})"
            trailing_params = rest.strip() if sep else ""
            if trailing_params:
                return (
                    f"{source_master} {reversed_nodes} {source_master} "
                    f"{trailing_params}"
                )
            return f"{source_master} {reversed_nodes} {source_master}"

    if not instance_name:
        raise ValueError(
            f"Spectre source master {source_master!r} is missing an instance name"
        )
    if remainder:
        return f"{instance_name} {nodes} {source_master} {remainder}"
    return f"{instance_name} {nodes} {source_master}"


def _ensure_unique_instance_name(netlist: SpectreNetlist, name: str) -> None:
    """Reject duplicate top-level source/instance names like Spectre does."""
    existing_names = (
        [source.name for source in netlist.sources]
        + [instance.name for instance in netlist.instances]
    )
    if name in existing_names:
        raise ValueError(
            f"Spectre duplicate instance name {name!r} is already defined"
        )


def _parse_pwl_wave_values(
    wave_data: str,
    variables: Dict[str, float],
    source_name: str,
) -> List[float]:
    """Parse one Spectre PWL wave array for voltage or current sources."""
    wave_vals = []
    for tok in _split_pwl_wave_tokens(wave_data, source_name):
        tok = tok.strip()
        if not tok:
            continue
        if _is_inline_wave_arithmetic(tok):
            raise ValueError(
                f"Spectre-incompatible PWL wave token {tok!r} in source "
                f"{source_name}: inline arithmetic inside wave=[...] is "
                "rejected; precompute the time/value literal"
            )
        val = _parse_suffix_number(tok)
        if val is None:
            try:
                val = _evaluate_complete_expr(tok, variables)
            except (ValueError, ZeroDivisionError):
                raise ValueError(
                    f"Invalid PWL wave token {tok!r} in source {source_name}"
                )
        wave_vals.append(val)
    return wave_vals


def _build_source(
    name: str,
    node_pos: str,
    node_neg: str,
    params: Dict[str, Any],
    *,
    kind: str = "voltage",
) -> SpectreSource:
    """Build a SpectreSource from parsed named parameters."""
    stype = str(params.get('type', 'dc')).lower()

    src = SpectreSource(
        name=name, node_pos=node_pos, node_neg=node_neg,
        source_type=stype, params=params, kind=kind,
    )
    return src


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_spectre(filepath: str) -> SpectreNetlist:
    """Parse a Spectre .scs netlist file into a SpectreNetlist AST."""
    filepath = Path(filepath).resolve()
    netlist = SpectreNetlist(source_dir=str(filepath.parent))

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        raw_lines = f.readlines()

    _validate_pwl_line_continuations(raw_lines)
    lines = _preprocess_lines(raw_lines)
    evaluator_vars = {}  # accumulated parameter variables

    # Extract title from first comment line if present
    for raw in raw_lines:
        stripped = raw.strip()
        if stripped.startswith('//'):
            netlist.title = stripped.lstrip('/').strip()
            break

    for idx in range(len(lines)):
        pass  # handled by while loop below

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        line = _normalize_source_keyword_instance(line)
        low = line.lower().strip()
        tokens = low.split()
        first = tokens[0] if tokens else ""

        if low.startswith("simulator("):
            raise ValueError(
                "Unsupported simulator language directive: use "
                "`simulator lang=spectre`, not parenthesized syntax"
            )

        # simulatorOptions
        if first == 'simulatoroptions':
            _parse_simulator_options(line, netlist, evaluator_vars)
            idx += 1
            continue

        # Named Spectre options analysis: "options options reltol=..."
        if first == 'options' and len(tokens) >= 2 and tokens[1] == 'options':
            _parse_simulator_options(line, netlist, evaluator_vars)
            idx += 1
            continue

        # Skip simulator language directive
        if first == 'simulator':
            idx += 1
            continue

        # Global ground
        if first == 'global':
            parts = line.split()
            if len(parts) >= 2:
                netlist.ground = parts[1]
            idx += 1
            continue

        # Parameters block
        if first == 'parameters':
            _parse_parameters(line, evaluator_vars)
            netlist.parameters = dict(evaluator_vars)
            idx += 1
            continue

        # Include (process models)
        if first == 'include':
            _parse_include(line, netlist)
            idx += 1
            continue

        # ahdl_include
        if first == 'ahdl_include':
            rest = line[len('ahdl_include'):].strip()
            path, trailing = _split_quoted_path(rest, "ahdl_include")
            if trailing:
                raise ValueError(
                    f"Malformed ahdl_include options: {trailing!r}"
                )
            netlist.ahdl_includes.append(AhdlInclude(path=path))
            idx += 1
            continue

        # subckt block
        if first == 'subckt':
            idx = _parse_subckt_block(lines, idx, netlist, evaluator_vars)
            continue

        # Transient analysis: "tran tran ..." or "tran1 tran ..."
        if first == 'tran' or (
            len(tokens) >= 2 and tokens[1] == 'tran'
        ):
            _parse_tran(line, netlist, evaluator_vars)
            idx += 1
            continue

        # saveOptions, info, finalTimeOP, etc. — skip
        if first in {
            'saveoptions',
            'info',
            'finaltimeop',
            'modelparameter',
            'element',
            'outputparameter',
            'designparamvals',
            'primitives',
            'subckts',
        }:
            idx += 1
            continue

        # save statement
        if first == 'save':
            parts = line.split()
            for sig in parts[1:]:
                for name, fmt in _expand_save_signal(sig):
                    netlist.save_signals.append(name)
                    if fmt is not None:
                        netlist.save_formats[name] = fmt
            idx += 1
            continue

        # Cadence accepts supported two-terminal primitives with or without
        # parentheses. Normalize the bare form so both share one parser.
        if _is_bare_two_terminal_primitive_syntax(line):
            normalized = _parenthesize_bare_two_terminal_primitive(line)
            _parse_instance(normalized, netlist, evaluator_vars)
            idx += 1
            continue

        # Voltage source: Vname (node node) vsource ...
        if line[0] in ('V', 'v') and '(' in line:
            _parse_vsource(line, netlist, evaluator_vars)
            idx += 1
            continue

        # Instance: Iname (nodes) ModelName k=v ...
        # Any line starting with a name and containing parenthesized nodes
        if '(' in line and not low.startswith('save'):
            _parse_instance(line, netlist, evaluator_vars)
            idx += 1
            continue

        raise ValueError(
            f"Unsupported or malformed Spectre statement: {line!r}"
        )

    return netlist


def _split_parameter_assignments(rest: str) -> List[Tuple[str, str]]:
    """Split ``name=expression`` pairs while preserving spaced expressions."""
    assignments: List[Tuple[str, str]] = []
    pos = 0

    while True:
        while pos < len(rest) and rest[pos].isspace():
            pos += 1
        if pos >= len(rest):
            break

        name_match = re.match(r"[A-Za-z_]\w*", rest[pos:])
        if name_match is None:
            raise ValueError(
                f"Expected Spectre parameter name near {rest[pos:]!r}"
            )
        name = name_match.group(0)
        pos += len(name)
        while pos < len(rest) and rest[pos].isspace():
            pos += 1
        if pos >= len(rest) or rest[pos] != "=":
            raise ValueError(f"Expected '=' after Spectre parameter {name!r}")
        pos += 1
        while pos < len(rest) and rest[pos].isspace():
            pos += 1

        expr_start = pos
        expr_end = len(rest)
        next_assignment = len(rest)
        depth = 0
        quote: Optional[str] = None
        escaped = False
        while pos < len(rest):
            ch = rest[pos]
            if quote is not None:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                pos += 1
                continue
            if ch in {'"', "'"}:
                quote = ch
                pos += 1
                continue
            if ch == "(":
                depth += 1
                pos += 1
                continue
            if ch == ")":
                if depth == 0:
                    raise ValueError(
                        f"Unmatched ')' in Spectre parameter {name!r}"
                    )
                depth -= 1
                pos += 1
                continue
            if depth == 0 and ch.isspace():
                lookahead = pos
                while lookahead < len(rest) and rest[lookahead].isspace():
                    lookahead += 1
                next_name = re.match(r"[A-Za-z_]\w*", rest[lookahead:])
                if next_name is not None:
                    after_name = lookahead + len(next_name.group(0))
                    while after_name < len(rest) and rest[after_name].isspace():
                        after_name += 1
                    if after_name < len(rest) and rest[after_name] == "=":
                        expr_end = pos
                        next_assignment = lookahead
                        break
            pos += 1

        if quote is not None:
            raise ValueError(
                f"Unterminated quote in Spectre parameter {name!r}"
            )
        if depth != 0:
            raise ValueError(
                f"Unmatched '(' in Spectre parameter {name!r}"
            )
        expr = rest[expr_start:expr_end].strip()
        if not expr:
            raise ValueError(
                f"Missing expression for Spectre parameter {name!r}"
            )
        assignments.append((name, expr))
        pos = next_assignment

    if not assignments:
        raise ValueError("Spectre parameters statement has no assignments")
    return assignments


def _parse_parameters(line: str, variables: Dict[str, float]):
    """Parse a `parameters` line, evaluating expressions in order."""
    rest = line[len('parameters'):].strip()
    if rest.startswith('{'):
        raise ValueError(
            "Spectre-incompatible block-style parameters declaration; "
            "use `parameters name=value ...` on one statement"
        )

    try:
        assignments = _split_parameter_assignments(rest)
    except ValueError as exc:
        raise ValueError(f"Invalid Spectre parameters statement: {exc}") from exc

    for name, expr in assignments:
        try:
            variables[name] = _evaluate_complete_expr(expr, variables)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(
                f"Invalid Spectre parameter {name!r} expression {expr!r}: {exc}"
            ) from exc


def _parse_simulator_options(line: str, netlist: SpectreNetlist,
                              variables: Dict[str, float]):
    """Parse simulatorOptions and keep numeric/text options."""
    params = _parse_named_params(line.split(), 2, variables)
    netlist.simulator_options.update(params)
    if 'temp' in params:
        netlist.temp = float(params['temp'])


def _parse_tran(line: str, netlist: SpectreNetlist,
                variables: Dict[str, float]):
    """Parse transient analysis: tran tran stop=val [maxstep=val] ..."""
    tokens = line.split()
    for token in tokens[1:]:
        if '=' not in token:
            continue
        key, value = token.split('=', 1)
        if (
            key.lower() in {"write", "writefinal"}
            and any(char in value for char in "/\\")
            and not (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {'"', "'"}
            )
        ):
            raise ValueError(
                f"Spectre {key}= output requires a quoted path; got {value!r}"
            )
    params = _parse_named_params(tokens, 1, variables)

    stop = params.get('stop', 0.0)
    if isinstance(stop, str):
        stop = evaluate_expr(stop, variables)

    maxstep = params.get('maxstep', None)
    if maxstep is not None and isinstance(maxstep, str):
        maxstep = evaluate_expr(maxstep, variables)

    # Default step: stop / 1000
    step = maxstep if maxstep is not None else float(stop) / 1000.0

    refine_factor = int(params.get('refine_factor', 16))
    refine_steps  = int(params.get('refine_steps',  8))
    errpreset = str(params.get('errpreset', ''))

    # The first token is the analysis instance name in both `tran tran` and
    # named forms such as `tran1 tran`.
    name = tokens[0] if tokens else 'tran'

    netlist.tran = SpectreTran(stop=float(stop), step=float(step), name=name,
                               refine_factor=refine_factor, refine_steps=refine_steps,
                               errpreset=errpreset)


def _normalize_node_name(name: str) -> str:
    """Normalize Cadence-escaped bus subscripts to plain angle-bracket form.

    Cadence Spectre exports bus pins as ``DOUT\\<9\\>`` (backslash-escaped).
    We strip the backslashes so node names become ``DOUT<9>`` throughout —
    cleaner in CSVs and consistent with what the user sees in Virtuoso.
    """
    return name.replace('\\<', '<').replace('\\>', '>')


def _expand_save_signal(token: str) -> List[Tuple[str, Optional[str]]]:
    """Expand one save token into [(signal, format), ...]."""
    token = _normalize_node_name(token.strip())
    if not token or token.startswith('options') or token.lower() == 'time':
        return []

    angle_inner_range = re.fullmatch(r'(.+?<)(-?\d+):(-?\d+)(>)', token)
    if angle_inner_range:
        prefix, hi_s, lo_s, suffix = angle_inner_range.groups()
        hi = int(hi_s)
        lo = int(lo_s)
        step = -1 if hi >= lo else 1
        return [(f"{prefix}{idx}{suffix}", None) for idx in range(hi, lo + step, step)]

    bus_range = re.fullmatch(r'(.+?<)(-?\d+)(>):(-?\d+)', token)
    if bus_range:
        prefix, hi_s, suffix, lo_s = bus_range.groups()
        hi = int(hi_s)
        lo = int(lo_s)
        step = -1 if hi >= lo else 1
        return [(f"{prefix}{idx}{suffix}", None) for idx in range(hi, lo + step, step)]

    square_range = re.fullmatch(r'(.+?)\[(-?\d+):(-?\d+)\]', token)
    if square_range:
        prefix, hi_s, lo_s = square_range.groups()
        hi = int(hi_s)
        lo = int(lo_s)
        step = -1 if hi >= lo else 1
        return [(f"{prefix}{idx}", None) for idx in range(hi, lo + step, step)]

    square_bit = re.fullmatch(r'(.+?)\[(-?\d+)\]', token)
    if square_bit:
        prefix, idx_s = square_bit.groups()
        return [(f"{prefix}{int(idx_s)}", None)]

    if ':' in token:
        name, fmt = token.split(':', 1)
        return [(name, fmt)]

    return [(token, None)]


def _extract_nodes(line: str) -> Tuple[str, List[str], str]:
    """Extract (name, nodes, remainder) from a line like 'Vname (n1 n2) rest...'

    Node names are normalized: Cadence escaped-bus notation ``DOUT\\<9\\>``
    is converted to plain ``DOUT<9>``.
    """
    # Find the first '(' and matching ')'
    paren_start = line.index('(')
    paren_end = line.index(')', paren_start)

    name_text = line[:paren_start].strip()
    name_parts = name_text.split()
    if len(name_parts) != 1:
        raise ValueError(
            "Spectre instance/source syntax expects exactly one name before "
            f"the node list; got {name_text!r}"
        )
    name = name_parts[0]
    nodes_str = line[paren_start + 1:paren_end].strip()
    remainder = line[paren_end + 1:].strip()

    nodes = [_normalize_node_name(n.strip("[]")) for n in nodes_str.split()]
    return name, nodes, remainder


def _parse_vsource(line: str, netlist: SpectreNetlist,
                   variables: Dict[str, float]):
    """Parse a voltage source: Vname (n+ n-) vsource type=X dc=Y ..."""
    name, nodes, remainder = _extract_nodes(line)

    if len(nodes) != 2:
        raise ValueError(
            f"Spectre source {name!r} requires exactly two terminals"
        )

    node_pos = nodes[0]
    node_neg = nodes[1]

    # remainder: "vsource dc=0.9 type=dc"
    tokens = remainder.split()

    if not tokens or tokens[0].lower() != 'vsource':
        raise ValueError(
            f"Spectre voltage source {name!r} is missing the vsource master"
        )
    param_start = 1

    # Handle bracket-delimited wave= for PWL
    # Rejoin remainder after vsource to handle wave=[...] properly
    param_str = ' '.join(tokens[param_start:])

    # Check for wave=[...] pattern
    wave_data = None
    wave_match = re.search(r'wave\s*=\s*\[([^\]]*)\]', param_str)
    if wave_match:
        wave_text = wave_match.group(1)
        wave_data = wave_text
        # Remove wave=[...] from param_str
        param_str = param_str[:wave_match.start()] + param_str[wave_match.end():]

    if re.search(r'(^|\s)\([^)]*\)', param_str):
        raise ValueError(
            f"Spectre-incompatible parenthesized vsource parameter list in source {name}: "
            "use named source parameters such as val0=, val1=, delay=, rise=, "
            "fall=, width=, and period=."
        )

    params = _parse_named_params(param_str.split(), 0, variables)

    if wave_data is not None:
        params['wave'] = _parse_pwl_wave_values(wave_data, variables, name)

    src = _build_source(name, node_pos, node_neg, params)
    _ensure_unique_instance_name(netlist, name)
    netlist.sources.append(src)


def _parse_instance(line: str, netlist: SpectreNetlist,
                    variables: Dict[str, float]):
    """Parse an instance: Iname (n1 n2 ...) ModelName k=v ..."""
    name, nodes, remainder = _extract_nodes(line)

    tokens = remainder.split()
    if not tokens:
        return

    # First non-param token after nodes is the model name
    model_name = None
    param_tokens = []

    for tok in tokens:
        if '=' in tok:
            param_tokens.append(tok)
        elif model_name is None:
            model_name = tok
        else:
            param_tokens.append(tok)

    if model_name is None:
        return

    # If model_name is a primitive source, this is a source, not an instance.
    if model_name.lower() == 'vsource':
        _parse_vsource(line, netlist, variables)
        return
    if model_name.lower() == 'isource':
        if len(nodes) != 2:
            raise ValueError(
                f"Spectre source {name!r} requires exactly two terminals"
            )
        param_str = ' '.join(param_tokens)
        wave_data = None
        wave_match = re.search(r'wave\s*=\s*\[([^\]]*)\]', param_str)
        if wave_match:
            wave_data = wave_match.group(1)
            param_str = param_str[:wave_match.start()] + param_str[wave_match.end():]
        params = _parse_named_params(param_str.split(), 0, variables)
        if wave_data is not None:
            params['wave'] = _parse_pwl_wave_values(wave_data, variables, name)
        _ensure_unique_instance_name(netlist, name)
        netlist.sources.append(
            _build_source(name, nodes[0], nodes[1], params, kind="current")
        )
        return

    params = _parse_named_params(param_tokens, 0, variables)

    _ensure_unique_instance_name(netlist, name)
    netlist.instances.append(SpectreInstance(
        name=name, nodes=nodes, model_name=model_name, params=params,
    ))


# ---------------------------------------------------------------------------
# Include parser
# ---------------------------------------------------------------------------

def _split_quoted_path(rest: str, statement: str) -> Tuple[str, str]:
    rest = rest.strip()
    if not rest or rest[0] not in {'"', "'"}:
        raise ValueError(f"{statement} requires a quoted path")
    quote = rest[0]
    end = rest.find(quote, 1)
    if end <= 1:
        raise ValueError(f"{statement} requires a non-empty quoted path")
    return rest[1:end], rest[end + 1:].strip()


def _parse_include(line: str, netlist: SpectreNetlist):
    """Parse include "path" [section=X] -> SpectreInclude."""
    rest = line[len("include"):].strip()
    path, trailing = _split_quoted_path(rest, "include")
    section = None
    if trailing:
        match = re.fullmatch(
            r"section\s*=\s*(\S+)",
            trailing,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise ValueError(f"Malformed include options: {trailing!r}")
        section = match.group(1)

    netlist.includes.append(SpectreInclude(path=path, section=section))
    if section is None and Path(path).suffix.lower() == ".va":
        netlist.ahdl_includes.append(AhdlInclude(path=path))


# ---------------------------------------------------------------------------
# Subckt block parser
# ---------------------------------------------------------------------------

def _parse_mosfet_line(line: str, variables: Dict[str, float]) -> Optional[SpectreMosfet]:
    """Parse a MOSFET line: M88 (d g s b) model_name k=v ... -> SpectreMosfet."""
    try:
        name, nodes, remainder = _extract_nodes(line)
    except ValueError:
        return None

    tokens = remainder.split()
    if not tokens:
        return None

    model = None
    param_tokens = []
    for tok in tokens:
        if '=' in tok:
            param_tokens.append(tok)
        elif model is None:
            model = tok
        else:
            param_tokens.append(tok)

    if model is None:
        return None

    params = _parse_named_params(param_tokens, 0, variables)
    return SpectreMosfet(name=name, nodes=nodes, model=model, params=params)


def _parse_subckt_block(lines: List[str], start_idx: int,
                        netlist: SpectreNetlist,
                        variables: Dict[str, float]) -> int:
    """Parse a subckt...ends block. Returns index of next line after ends."""
    header = lines[start_idx]
    # "subckt name port1 port2 ..."
    parts = header.split()
    subckt_name = parts[1] if len(parts) > 1 else 'unknown'
    ports = parts[2:] if len(parts) > 2 else []

    subckt = SpectreSubckt(name=subckt_name, ports=ports)
    idx = start_idx + 1

    while idx < len(lines):
        line = lines[idx]
        low = line.lower().strip()

        # End of subckt
        if low.startswith('ends'):
            idx += 1
            break

        # M-device line (starts with M or m, has parenthesized nodes)
        if line[0] in ('M', 'm') and '(' in line:
            mosfet = _parse_mosfet_line(line, variables)
            if mosfet:
                subckt.mosfets.append(mosfet)
                subckt.body_lines.append(line)
                idx += 1
                continue

        # Instance inside subckt (e.g. subckt calls)
        if '(' in line:
            try:
                iname, inodes, iremainder = _extract_nodes(line)
                itokens = iremainder.split()
                imodel = None
                iparam_tokens = []
                for tok in itokens:
                    if '=' in tok:
                        iparam_tokens.append(tok)
                    elif imodel is None:
                        imodel = tok
                if imodel and imodel.lower() != 'vsource':
                    iparams = _parse_named_params(iparam_tokens, 0, variables)
                    subckt.instances.append(SpectreInstance(
                        name=iname, nodes=inodes, model_name=imodel, params=iparams,
                    ))
            except ValueError:
                pass
            subckt.body_lines.append(line)
            idx += 1
            continue

        # Any other line inside subckt — store as raw body
        subckt.body_lines.append(line)
        idx += 1

    netlist.subckts.append(subckt)
    return idx


# ---------------------------------------------------------------------------
# Detection helper
# ---------------------------------------------------------------------------

def has_transistors(netlist: SpectreNetlist) -> bool:
    """Check if a netlist contains transistor-level devices (subckts with MOSFETs
    or top-level M-prefix instances)."""
    if netlist.subckts:
        return True
    # Check for top-level M-prefix instances
    for inst in netlist.instances:
        if inst.name.upper().startswith('M'):
            return True
    return False
