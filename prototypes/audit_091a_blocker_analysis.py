#!/usr/bin/env python3
"""Audit 091a: Identify Verilog-A AST features that block models from
qualifying as whole-segment candidates.

Iterates over every .va file in the vabench release tree, parses the AST,
walks the analog block, and records which constructs appear. Then
cross-references with the Rust coverage manifest to focus on the 249
models that currently sit in the "transition_target_ir + ordered_shadow"
bucket — they have transition IR but no whole-segment fastpath.

Output: ranked blocker table answering "if we lift feature X, how many
additional models become whole-segment-eligible candidates?"
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

from evas.compiler.parser import parse
from evas.compiler.ast_nodes import (
    Assignment, BinaryExpr, Block, BranchAccess, CaseStatement, Contribution,
    EventStatement, ForStatement, FunctionCall, IfStatement, MethodCall,
    TernaryExpr, UnaryExpr, WhileStatement, ArrayAccess,
)


REPO = Path(__file__).resolve().parents[2]
VABENCH = REPO / "behavioral-veriloga-eval" / "benchmark-vabench-release-v1" / "tasks"
MANIFEST = (
    REPO / "behavioral-veriloga-eval" / "speed-optimization" / "reports"
    / "current_release_rust_coverage_manifest_20260604.json"
)
BIT_OPS = {">>", "<<", "&", "|", "^", "~"}
MATH_FNS = {"floor", "ceil", "abs", "min", "max", "pow", "sqrt", "ln", "log",
            "exp", "sin", "cos", "tan", "tanh"}
SYSTEM_TASKS = {"$strobe", "$display", "$write", "$fwrite", "$fdisplay",
                "$fopen", "$fclose", "$fstrobe", "$random", "$abstime",
                "$temperature", "$vt", "$bound_step", "$rdist_normal",
                "$dist_uniform"}


def _walk_stmts(node, visitor):
    """DFS walk over statements/blocks, calling visitor(stmt) on each."""
    if node is None:
        return
    visitor(node)
    if isinstance(node, Block):
        for s in node.statements:
            _walk_stmts(s, visitor)
    elif isinstance(node, IfStatement):
        _walk_stmts(node.then_body, visitor)
        _walk_stmts(node.else_body, visitor)
    elif isinstance(node, ForStatement):
        _walk_stmts(node.init, visitor)
        _walk_stmts(node.body, visitor)
        _walk_stmts(node.update, visitor)
    elif isinstance(node, WhileStatement):
        _walk_stmts(node.body, visitor)
    elif isinstance(node, CaseStatement):
        for it in getattr(node, "items", []):
            _walk_stmts(getattr(it, "body", None), visitor)
    elif isinstance(node, EventStatement):
        _walk_stmts(node.body, visitor)


def _walk_exprs(expr, visitor):
    """DFS walk over expressions, calling visitor(expr) on each."""
    if expr is None:
        return
    visitor(expr)
    if isinstance(expr, BinaryExpr):
        _walk_exprs(expr.left, visitor)
        _walk_exprs(expr.right, visitor)
    elif isinstance(expr, UnaryExpr):
        _walk_exprs(expr.operand, visitor)
    elif isinstance(expr, TernaryExpr):
        _walk_exprs(expr.cond, visitor)
        _walk_exprs(expr.true_expr, visitor)
        _walk_exprs(expr.false_expr, visitor)
    elif isinstance(expr, FunctionCall):
        for a in expr.args:
            _walk_exprs(a, visitor)
    elif isinstance(expr, MethodCall):
        for a in expr.args:
            _walk_exprs(a, visitor)
    elif isinstance(expr, ArrayAccess):
        _walk_exprs(expr.index, visitor)
    elif isinstance(expr, BranchAccess):
        for sub in (expr.node1_index, expr.node1_index2,
                    expr.node2_index, expr.node2_index2):
            _walk_exprs(sub, visitor)


def extract_features(va_path: Path) -> Dict[str, Any]:
    """Parse a .va file and return a dict of detected AST features."""
    try:
        text = va_path.read_text(errors="ignore")
        result = parse(text)
    except Exception as e:
        return {"parse_error": str(e)[:200]}
    # parser returns a single Module object directly
    mod = result if hasattr(result, "analog_block") else None
    if mod is None and isinstance(result, list) and result:
        mod = result[0]
    if mod is None:
        return {"parse_error": "no module returned"}
    if not mod.analog_block:
        return {"no_analog_block": True}

    features = Counter()
    event_kinds = Counter()
    transition_output_count = 0
    if_stmt_depth = Counter()
    event_if_nesting = []  # max nesting per event

    # Statement-level walk
    def stmt_visit(s):
        nonlocal transition_output_count
        if isinstance(s, IfStatement):
            features["if_stmt"] += 1
        elif isinstance(s, ForStatement):
            features["for_loop_in_body"] += 1
        elif isinstance(s, WhileStatement):
            features["while_loop_in_body"] += 1
        elif isinstance(s, CaseStatement):
            features["case_stmt"] += 1
        elif isinstance(s, EventStatement):
            features["event_stmt"] += 1
            ev = s.event
            ev_type = getattr(ev, "event_type", None) or "combined"
            name = str(ev_type)
            event_kinds[name] += 1
        elif isinstance(s, Contribution):
            features["contribution"] += 1
            # Is the contribution body a transition() call (possibly affine)?
            has_trans = False
            def find_trans(e):
                nonlocal has_trans
                if isinstance(e, FunctionCall) and e.name == "transition":
                    has_trans = True
            _walk_exprs(s.expr, find_trans)
            if has_trans:
                transition_output_count += 1
        elif isinstance(s, Assignment):
            features["assignment"] += 1

    _walk_stmts(mod.analog_block.body, stmt_visit)
    features["transition_output_count"] = transition_output_count

    # Per-event nesting (if/case depth inside event body)
    if mod.analog_block:
        for s in mod.analog_block.body.statements:
            if isinstance(s, EventStatement):
                max_depth = [0]
                def nest_visit(node, _depth=[0]):
                    if isinstance(node, (IfStatement, CaseStatement)):
                        _depth[0] += 1
                        max_depth[0] = max(max_depth[0], _depth[0])
                        # recurse handled by _walk_stmts; depth tracked here
                # use a simpler approach: count if statements inside this event body
                inner_if = [0]
                def cnt(n):
                    if isinstance(n, IfStatement):
                        inner_if[0] += 1
                _walk_stmts(s.body, cnt)
                event_if_nesting.append(inner_if[0])

    if event_if_nesting:
        features["max_if_per_event_body"] = max(event_if_nesting)
        features["sum_if_in_event_bodies"] = sum(event_if_nesting)

    # Expression-level walk over the whole analog block
    def expr_visit(e):
        if isinstance(e, BinaryExpr) and e.op in BIT_OPS:
            features[f"bitop_{e.op}"] += 1
            features["any_bit_op"] += 1
        if isinstance(e, FunctionCall):
            if e.name in MATH_FNS:
                features[f"math_{e.name}"] += 1
                features["any_math_helper"] += 1
            if e.name in SYSTEM_TASKS:
                features[f"systask_{e.name}"] += 1
                features["any_system_task"] += 1
        if isinstance(e, ArrayAccess):
            features["array_access"] += 1
        if isinstance(e, BranchAccess):
            if (e.node1_index is not None or e.node1_index2 is not None
                or e.node2_index is not None or e.node2_index2 is not None):
                features["dynamic_bus_access"] += 1

    def stmt_to_exprs(s):
        if isinstance(s, IfStatement):
            _walk_exprs(s.cond, expr_visit)
        elif isinstance(s, ForStatement):
            _walk_exprs(s.cond, expr_visit)
        elif isinstance(s, WhileStatement):
            _walk_exprs(s.cond, expr_visit)
        elif isinstance(s, CaseStatement):
            _walk_exprs(s.expr, expr_visit)
        elif isinstance(s, Contribution):
            _walk_exprs(s.expr, expr_visit)
        elif isinstance(s, Assignment):
            _walk_exprs(s.value, expr_visit)
            _walk_exprs(s.target, expr_visit)

    _walk_stmts(mod.analog_block.body, stmt_to_exprs)

    # State variable counts
    features["state_scalars"] = sum(1 for v in mod.variables if not v.is_array)
    features["state_arrays"] = sum(1 for v in mod.variables if v.is_array)
    features["parameters"] = len(mod.parameters)

    features["event_kinds"] = dict(event_kinds)
    return dict(features)


def main():
    if not MANIFEST.exists():
        raise SystemExit(f"missing manifest: {MANIFEST}")
    if not VABENCH.exists():
        raise SystemExit(f"missing vabench tree: {VABENCH}")
    manifest = json.loads(MANIFEST.read_text())

    # Find all 249 models in the transition_target_ir + ordered_shadow bucket.
    target_paths: List[Dict[str, Any]] = []
    seen_hashes: Set[str] = set()
    for m in manifest["models"]:
        sigs = set(m.get("rust_signals", []))
        if ("whole_segment_candidate" not in sigs and
            "transition_target_ir" in sigs and
            "ordered_transition_shadow" in sigs):
            target_paths.append(m)

    # Deduplicate by sha256 (152 unique sources in manifest)
    unique_targets = []
    for m in target_paths:
        h = m.get("sha256", "")
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_targets.append(m)

    print(f"Target bucket: 'transition_target_ir + ordered_shadow' (no whole_segment)")
    print(f"  Total rows : {len(target_paths)}")
    print(f"  Unique sha : {len(unique_targets)}")
    print()

    # Extract features per unique model
    feature_index: Dict[str, Dict[str, Any]] = {}
    parse_errors = 0
    for m in unique_targets:
        # manifest paths are relative to behavioral-veriloga-eval/
        path = REPO / "behavioral-veriloga-eval" / m["path"]
        if not path.exists():
            continue
        feats = extract_features(path)
        if "parse_error" in feats:
            parse_errors += 1
            continue
        feature_index[m["sha256"]] = {"path": m["path"], "module": m.get("module"), **feats}

    if parse_errors:
        print(f"(skipped {parse_errors} files due to parse errors)\n")

    print(f"Analyzed {len(feature_index)} unique models.\n")

    # Compute per-feature "blocker" coverage: how many models have this feature?
    feature_blocks = Counter()
    for sha, feats in feature_index.items():
        for key, val in feats.items():
            if key in ("path", "module", "event_kinds"):
                continue
            if isinstance(val, (int, float)) and val > 0:
                feature_blocks[key] += 1

    # Ranked output
    total = len(feature_index)
    print(f"=== Per-feature presence among the {total} target models ===")
    print(f"  {'feature':40s} {'count':>6s} {'%':>6s}")
    print(f"  {'-'*40} {'-'*6} {'-'*6}")
    for feat, count in feature_blocks.most_common(50):
        pct = count / total * 100
        print(f"  {feat:40s} {count:6d} {pct:5.1f}%")

    # Combos: how many have BOTH bit_op + state machine, etc.
    print()
    print("=== Compound coverage (what unlocks the most models?) ===")

    def matches(feats, predicate):
        return predicate(feats)

    scenarios = [
        ("no advanced features (current scope)", lambda f: (
            not f.get("any_bit_op")
            and not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
            and not f.get("any_system_task")
            and not f.get("array_access")
            and not f.get("dynamic_bus_access")
        )),
        ("only need: bit ops", lambda f: f.get("any_bit_op")),
        ("only need: any math helper (floor/ceil/abs/...)", lambda f: f.get("any_math_helper")),
        ("only need: $strobe / $display I/O", lambda f: f.get("any_system_task")),
        ("only need: state array access", lambda f: f.get("array_access")),
        ("only need: dynamic bus access (V(bus[i]))", lambda f: f.get("dynamic_bus_access")),
        ("only need: for loop in body", lambda f: f.get("for_loop_in_body")),
        ("only need: while loop in body", lambda f: f.get("while_loop_in_body")),
        ("nested if in event body (FSM-like)", lambda f: f.get("max_if_per_event_body", 0) >= 2),
        ("multi-output (>=3 transition outputs)", lambda f: f.get("transition_output_count", 0) >= 3),
    ]
    print(f"  {'scenario':52s} {'models':>7s} {'%':>6s}")
    print(f"  {'-'*52} {'-'*7} {'-'*6}")
    for label, pred in scenarios:
        count = sum(1 for f in feature_index.values() if matches(f, pred))
        pct = count / total * 100
        print(f"  {label:52s} {count:7d} {pct:5.1f}%")

    # "If we lift these blockers, how many new models qualify?"
    print()
    print("=== Incremental coverage scenarios (cumulative) ===")
    base_pred = lambda f: True  # start with everything
    print(f"  {'lift':52s} {'models':>7s} {'cumul %':>8s}")
    increments = [
        ("none (baseline: all 249 models)", lambda f: True),
        ("+ allow $strobe / $display", lambda f: (
            not f.get("any_bit_op")
            and not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
            and not f.get("array_access")
            and not f.get("dynamic_bus_access")
        )),
        ("+ allow bit ops (>>, &, |, ^)", lambda f: (
            not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
            and not f.get("array_access")
            and not f.get("dynamic_bus_access")
        )),
        ("+ allow math helpers (floor/ceil/abs)", lambda f: (
            not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
            and not f.get("array_access")
            and not f.get("dynamic_bus_access")
        )),
        ("+ allow state array access (state[i])", lambda f: (
            not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
            and not f.get("dynamic_bus_access")
        )),
        ("+ allow dynamic bus V(bus[i])", lambda f: (
            not f.get("for_loop_in_body")
            and not f.get("while_loop_in_body")
        )),
        ("+ allow for/while loops", lambda f: True),
    ]
    for label, pred in increments:
        count = sum(1 for f in feature_index.values() if pred(f))
        pct = count / total * 100
        print(f"  {label:52s} {count:7d} {pct:7.1f}%")


if __name__ == "__main__":
    main()
