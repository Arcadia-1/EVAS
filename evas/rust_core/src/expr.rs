use crate::abi::*;
use crate::event::*;
use crate::util::*;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};

// IR evaluators shared by Python-driven fast paths and Rust-owned programs.

pub fn evaluate_static_affine_ops(
    ops: &[EvasRustStaticAffineOp],
    values: &mut [f64],
) -> Result<(), i32> {
    for op in ops {
        if op.read_node_id >= values.len() || op.write_node_id >= values.len() {
            return Err(-3);
        }
        values[op.write_node_id] = op.bias + op.gain * values[op.read_node_id];
    }
    Ok(())
}

pub fn evaluate_static_linear_ops(
    ops: &[EvasRustLinearOp],
    terms: &[EvasRustLinearTerm],
    conditions: &[EvasRustLinearCondition],
    node_values: &mut [f64],
    state_values: &mut [f64],
) -> Result<(), i32> {
    for op in ops {
        let term_end = op.term_start.checked_add(op.term_count).ok_or(-6)?;
        if term_end > terms.len() {
            return Err(-7);
        }

        let mut value = evaluate_linear_value(
            op.bias,
            &terms[op.term_start..term_end],
            node_values,
            state_values,
        )?;
        if op.condition_id != CONDITION_NONE {
            if op.condition_id >= conditions.len() {
                return Err(-14);
            }
            let condition = &conditions[op.condition_id];
            if !evaluate_condition(condition, terms, node_values, state_values)? {
                let false_term_end = op
                    .false_term_start
                    .checked_add(op.false_term_count)
                    .ok_or(-15)?;
                if false_term_end > terms.len() {
                    return Err(-16);
                }
                value = evaluate_linear_value(
                    op.false_bias,
                    &terms[op.false_term_start..false_term_end],
                    node_values,
                    state_values,
                )?;
            }
        }

        match op.target_kind {
            TARGET_NODE => {
                if op.target_id >= node_values.len() {
                    return Err(-11);
                }
                node_values[op.target_id] = value;
            }
            TARGET_STATE => {
                if op.target_id >= state_values.len() {
                    return Err(-12);
                }
                state_values[op.target_id] = if op.target_integer != 0 {
                    to_veriloga_integer(value) as f64
                } else {
                    value
                };
            }
            _ => return Err(-13),
        }
    }
    Ok(())
}

pub fn evaluate_body_ir_ops(
    stmt_ops: &[EvasRustBodyStmtOp],
    expr_ops: &[EvasRustBodyExprOp],
    node_values: &mut [f64],
    state_values: &mut [f64],
    param_values: &[f64],
) -> Result<(), i32> {
    evaluate_body_ir_ops_at_time(
        stmt_ops,
        expr_ops,
        node_values,
        state_values,
        param_values,
        0.0,
    )
}

pub fn evaluate_body_ir_ops_at_time(
    stmt_ops: &[EvasRustBodyStmtOp],
    expr_ops: &[EvasRustBodyExprOp],
    node_values: &mut [f64],
    state_values: &mut [f64],
    param_values: &[f64],
    time: f64,
) -> Result<(), i32> {
    let mut ignored_bound_step = f64::INFINITY;
    evaluate_body_ir_ops_at_time_impl(
        stmt_ops,
        expr_ops,
        node_values,
        state_values,
        param_values,
        time,
        &mut ignored_bound_step,
        false,
        None,
        None,
    )
}

pub(crate) struct RustSideEffectLog<'a> {
    pub(crate) kinds: &'a mut [u8],
    pub(crate) spec_ids: &'a mut [usize],
    pub(crate) arg_starts: &'a mut [usize],
    pub(crate) arg_counts: &'a mut [usize],
    pub(crate) times: &'a mut [f64],
    pub(crate) values: &'a mut [f64],
    pub(crate) count: &'a mut usize,
    pub(crate) value_count: &'a mut usize,
}

impl<'a> RustSideEffectLog<'a> {
    pub(crate) fn push(
        &mut self,
        kind: u8,
        spec_id: usize,
        time: f64,
        args: &[f64],
    ) -> Result<(), i32> {
        if *self.count >= self.kinds.len()
            || *self.count >= self.spec_ids.len()
            || *self.count >= self.arg_starts.len()
            || *self.count >= self.arg_counts.len()
            || *self.count >= self.times.len()
        {
            return Err(-2220);
        }
        let start = *self.value_count;
        let end = start.checked_add(args.len()).ok_or(-2221)?;
        if end > self.values.len() {
            return Err(-2222);
        }
        self.kinds[*self.count] = kind;
        self.spec_ids[*self.count] = spec_id;
        self.arg_starts[*self.count] = start;
        self.arg_counts[*self.count] = args.len();
        self.times[*self.count] = time;
        self.values[start..end].copy_from_slice(args);
        *self.count += 1;
        *self.value_count = end;
        Ok(())
    }
}

pub(crate) struct RustFileIoRuntime<'a> {
    specs: &'a [EvasRustFileIoSpec],
    string_bytes: &'a [u8],
    target_ids: &'a [usize],
    target_integers: &'a [u8],
    handles: Vec<Option<BufReader<File>>>,
}

impl<'a> RustFileIoRuntime<'a> {
    pub(crate) fn new(
        specs: &'a [EvasRustFileIoSpec],
        string_bytes: &'a [u8],
        target_ids: &'a [usize],
        target_integers: &'a [u8],
    ) -> Self {
        Self {
            specs,
            string_bytes,
            target_ids,
            target_integers,
            handles: Vec::new(),
        }
    }

    fn spec(&self, spec_id: usize, expected_kind: u8) -> Result<&EvasRustFileIoSpec, i32> {
        let spec = self.specs.get(spec_id).ok_or(-2340)?;
        if spec.kind != expected_kind {
            return Err(-2341);
        }
        Ok(spec)
    }

    fn spec_string(&self, start: usize, len: usize) -> Result<String, i32> {
        let end = start.checked_add(len).ok_or(-2342)?;
        if end > self.string_bytes.len() {
            return Err(-2343);
        }
        std::str::from_utf8(&self.string_bytes[start..end])
            .map(|s| s.to_string())
            .map_err(|_| -2344)
    }

    fn primary_string(&self, spec: &EvasRustFileIoSpec) -> Result<String, i32> {
        self.spec_string(spec.string_start, spec.string_len)
    }

    fn aux_string(&self, spec: &EvasRustFileIoSpec) -> Result<String, i32> {
        self.spec_string(spec.aux_start, spec.aux_len)
    }

    pub(crate) fn fopen(&mut self, spec_id: usize) -> Result<Option<f64>, i32> {
        let spec = *self.spec(spec_id, RUST_FILE_SPEC_FOPEN)?;
        let mode = self.aux_string(&spec)?;
        if !mode.contains('r') {
            return Ok(None);
        }
        let filename = self.primary_string(&spec)?;
        let file = File::open(filename).map_err(|_| -2345)?;
        self.handles.push(Some(BufReader::new(file)));
        Ok(Some(self.handles.len() as f64))
    }

    fn handle_mut(&mut self, fd: f64) -> Option<&mut BufReader<File>> {
        if !fd.is_finite() {
            return None;
        }
        let rounded = to_veriloga_integer_trunc(fd);
        if rounded <= 0 {
            return None;
        }
        let idx = (rounded as usize).saturating_sub(1);
        self.handles.get_mut(idx).and_then(|slot| slot.as_mut())
    }

    pub(crate) fn fclose(&mut self, fd: f64) -> bool {
        if !fd.is_finite() {
            return false;
        }
        let rounded = to_veriloga_integer_trunc(fd);
        if rounded <= 0 {
            return false;
        }
        let idx = (rounded as usize).saturating_sub(1);
        if let Some(slot) = self.handles.get_mut(idx) {
            if slot.is_some() {
                *slot = None;
                return true;
            }
        }
        false
    }

    pub(crate) fn ftell(&mut self, fd: f64) -> f64 {
        self.handle_mut(fd)
            .and_then(|handle| handle.stream_position().ok())
            .map(|pos| pos as f64)
            .unwrap_or(-1.0)
    }

    pub(crate) fn feof(&mut self, fd: f64) -> f64 {
        let Some(handle) = self.handle_mut(fd) else {
            return 1.0;
        };
        let Ok(pos) = handle.stream_position() else {
            return 1.0;
        };
        let mut byte = [0_u8; 1];
        let at_eof = match handle.read(&mut byte) {
            Ok(0) => true,
            Ok(_) => false,
            Err(_) => true,
        };
        let _ = handle.seek(SeekFrom::Start(pos));
        if at_eof {
            1.0
        } else {
            0.0
        }
    }

    pub(crate) fn fseek(&mut self, fd: f64, offset: f64, whence: f64) -> f64 {
        let Some(handle) = self.handle_mut(fd) else {
            return -1.0;
        };
        let off = to_veriloga_integer_trunc(offset);
        let whence_i = to_veriloga_integer_trunc(whence);
        let seek_from = match whence_i {
            0 => SeekFrom::Start(off.max(0) as u64),
            1 => SeekFrom::Current(off),
            2 => SeekFrom::End(off),
            _ => return -1.0,
        };
        match handle.seek(seek_from) {
            Ok(_) => 0.0,
            Err(_) => -1.0,
        }
    }

    pub(crate) fn fgets_discard(&mut self, fd: f64) -> f64 {
        let Some(handle) = self.handle_mut(fd) else {
            return 0.0;
        };
        let mut line = String::new();
        match handle.read_line(&mut line) {
            Ok(0) | Err(_) => 0.0,
            Ok(_) => 1.0,
        }
    }

    fn next_scan_value(chars: &[char], mut pos: usize) -> (usize, Option<String>) {
        while pos < chars.len() && chars[pos].is_whitespace() {
            pos += 1;
        }
        let start = pos;
        while pos < chars.len() && !chars[pos].is_whitespace() && chars[pos] != ',' {
            pos += 1;
        }
        if pos == start {
            (pos, None)
        } else {
            (pos, Some(chars[start..pos].iter().collect()))
        }
    }

    pub(crate) fn fscanf(
        &mut self,
        spec_id: usize,
        fd: f64,
        state_values: &mut [f64],
    ) -> Result<f64, i32> {
        let spec = *self.spec(spec_id, RUST_FILE_SPEC_FSCANF)?;
        let fmt = self.primary_string(&spec)?;
        let target_end = spec
            .target_start
            .checked_add(spec.target_count)
            .ok_or(-2350)?;
        if target_end > self.target_ids.len() || target_end > self.target_integers.len() {
            return Err(-2351);
        }
        let Some(handle) = self.handle_mut(fd) else {
            return Ok(0.0);
        };
        let mut line = String::new();
        let bytes = handle.read_line(&mut line).map_err(|_| -2352)?;
        if bytes == 0 {
            return Ok(0.0);
        }
        let chars: Vec<char> = line.chars().collect();
        let mut input_pos = 0_usize;
        let mut assigned = 0_usize;
        let mut fmt_iter = fmt.chars().peekable();
        while let Some(ch) = fmt_iter.next() {
            if ch.is_whitespace() {
                while input_pos < chars.len() && chars[input_pos].is_whitespace() {
                    input_pos += 1;
                }
                continue;
            }
            if ch != '%' {
                if input_pos < chars.len() && chars[input_pos] == ch {
                    input_pos += 1;
                    continue;
                }
                break;
            }
            let mut suppress = false;
            if matches!(fmt_iter.peek(), Some('*')) {
                suppress = true;
                fmt_iter.next();
            }
            while matches!(fmt_iter.peek(), Some(c) if c.is_ascii_digit() || *c == '.') {
                fmt_iter.next();
            }
            while matches!(fmt_iter.peek(), Some('l' | 'L' | 'h' | 'H')) {
                fmt_iter.next();
            }
            let Some(spec_ch) = fmt_iter.next() else {
                break;
            };
            if spec_ch == '%' {
                if input_pos < chars.len() && chars[input_pos] == '%' {
                    input_pos += 1;
                    continue;
                }
                break;
            }
            let (next_pos, token) = Self::next_scan_value(&chars, input_pos);
            let Some(token) = token else {
                break;
            };
            input_pos = next_pos;
            if suppress {
                continue;
            }
            if assigned >= spec.target_count {
                break;
            }
            let value = match spec_ch.to_ascii_lowercase() {
                'd' => token
                    .parse::<f64>()
                    .map(to_veriloga_integer)
                    .map(|v| v as f64),
                'e' | 'f' | 'g' => token.parse::<f64>(),
                _ => break,
            };
            let Ok(mut value) = value else {
                break;
            };
            let target_idx = spec.target_start + assigned;
            let state_id = self.target_ids[target_idx];
            if state_id >= state_values.len() {
                return Err(-2353);
            }
            if self.target_integers[target_idx] != 0 {
                value = to_veriloga_integer(value) as f64;
            }
            state_values[state_id] = value;
            assigned += 1;
        }
        Ok(assigned as f64)
    }
}

pub(crate) fn evaluate_body_ir_ops_at_time_impl(
    stmt_ops: &[EvasRustBodyStmtOp],
    expr_ops: &[EvasRustBodyExprOp],
    node_values: &mut [f64],
    state_values: &mut [f64],
    param_values: &[f64],
    time: f64,
    bound_step_limit: &mut f64,
    capture_bound_step: bool,
    mut file_io: Option<&mut RustFileIoRuntime<'_>>,
    mut side_effect_log: Option<&mut RustSideEffectLog<'_>>,
) -> Result<(), i32> {
    let mut stack: Vec<f64> = Vec::with_capacity(32);
    let mut branch_active_stack: Vec<u8> = Vec::with_capacity(8);
    let mut loop_stack: Vec<(usize, usize)> = Vec::with_capacity(4);
    let mut pc = 0_usize;
    while pc < stmt_ops.len() {
        let stmt = stmt_ops[pc];
        match stmt.target_kind {
            BODY_STMT_WHILE => {
                let parent_active = branch_active_stack.iter().all(|flag| *flag != 0);
                if !parent_active {
                    let end_pc = find_matching_endwhile(stmt_ops, pc)?;
                    pc = end_pc.checked_add(1).ok_or(-2231)?;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let cond_value = stack.pop().ok_or(-2208)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                if cond_value == 0.0 {
                    if matches!(loop_stack.last(), Some((while_pc, _)) if *while_pc == pc) {
                        loop_stack.pop();
                    }
                    let end_pc = find_matching_endwhile(stmt_ops, pc)?;
                    pc = end_pc.checked_add(1).ok_or(-2231)?;
                    continue;
                }
                if !matches!(loop_stack.last(), Some((while_pc, _)) if *while_pc == pc) {
                    loop_stack.push((pc, 0));
                }
                branch_active_stack.push(1);
            }
            BODY_STMT_ENDWHILE => {
                let (while_pc, iterations) = *loop_stack.last().ok_or(-2232)?;
                let while_stmt = stmt_ops.get(while_pc).ok_or(-2233)?;
                let max_iters = if while_stmt.target_id == 0 {
                    4096
                } else {
                    while_stmt.target_id
                };
                let next_iterations = iterations.checked_add(1).ok_or(-2234)?;
                if next_iterations > max_iters {
                    return Err(-2235);
                }
                if let Some(frame) = loop_stack.last_mut() {
                    frame.1 = next_iterations;
                }
                if branch_active_stack.pop().is_none() {
                    return Err(-2236);
                }
                pc = while_pc;
                continue;
            }
            BODY_STMT_IF => {
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                let parent_active = branch_active_stack.iter().all(|flag| *flag != 0);
                if parent_active {
                    stack.clear();
                    evaluate_body_expr_segment(
                        &expr_ops[stmt.expr_start..expr_end],
                        node_values,
                        state_values,
                        param_values,
                        time,
                        &mut stack,
                    )?;
                    let cond_value = stack.pop().ok_or(-2208)?;
                    if !stack.is_empty() {
                        return Err(-2209);
                    }
                    branch_active_stack.push(if cond_value != 0.0 { 1 } else { 0 });
                } else {
                    branch_active_stack.push(0);
                }
            }
            BODY_STMT_ELSE => {
                if branch_active_stack.is_empty() {
                    return Err(-2213);
                }
                let last = branch_active_stack.len() - 1;
                let parent_active = branch_active_stack[..last].iter().all(|flag| *flag != 0);
                let previous_branch_active = branch_active_stack[last] != 0;
                branch_active_stack[last] = if parent_active && !previous_branch_active {
                    1
                } else {
                    0
                };
            }
            BODY_STMT_ENDIF => {
                if branch_active_stack.pop().is_none() {
                    return Err(-2214);
                }
            }
            BODY_STMT_LAST_CROSSING => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let init_id = stmt.target_id.checked_add(1).ok_or(-2280)?;
                let prev_t_id = stmt.target_id.checked_add(2).ok_or(-2281)?;
                let prev_x_id = stmt.target_id.checked_add(3).ok_or(-2282)?;
                if prev_x_id >= state_values.len() {
                    return Err(-2283);
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let expr_tol = pop1(&mut stack)?.abs();
                let time_tol = pop1(&mut stack)?.max(0.0);
                let direction = pop1(&mut stack)?;
                let mut value = pop1(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2284);
                }

                if state_values[init_id] == 0.0 {
                    state_values[init_id] = 1.0;
                    state_values[prev_t_id] = time;
                    state_values[prev_x_id] = value;
                    pc += 1;
                    continue;
                }

                let prev_value = state_values[prev_x_id];
                let prev_time = state_values[prev_t_id];
                let mut triggered = false;
                let mut trigger_direction = 0_i32;
                let mut trigger_went_beyond = false;
                let mut crossing_time = state_values[stmt.target_id];

                let interpolate_cross_time = || -> f64 {
                    let delta = value - prev_value;
                    let frac = if delta.abs() > 1.0e-30 {
                        (-prev_value / delta).clamp(0.0, 1.0)
                    } else {
                        0.0
                    };
                    prev_time + frac * (time - prev_time)
                };

                if direction >= 0.0 && prev_value < -expr_tol {
                    if value > expr_tol {
                        triggered = true;
                        trigger_direction = 1;
                        trigger_went_beyond = true;
                        crossing_time = interpolate_cross_time();
                    } else if value.abs() <= expr_tol {
                        triggered = true;
                        trigger_direction = 1;
                        crossing_time = interpolate_cross_time();
                    }
                }
                if !triggered && direction <= 0.0 && prev_value > expr_tol {
                    if value < -expr_tol {
                        triggered = true;
                        trigger_direction = -1;
                        trigger_went_beyond = true;
                        crossing_time = interpolate_cross_time();
                    } else if value.abs() <= expr_tol {
                        triggered = true;
                        trigger_direction = -1;
                        crossing_time = interpolate_cross_time();
                    }
                }

                if triggered {
                    let previous_crossing_time = state_values[stmt.target_id];
                    if previous_crossing_time < 0.0
                        || (crossing_time - previous_crossing_time).abs() > time_tol
                    {
                        state_values[stmt.target_id] = crossing_time;
                    }
                    if trigger_went_beyond {
                        let sign_eps = expr_tol.max(1.0e-18);
                        if trigger_direction < 0 {
                            value = value.min(-sign_eps);
                        } else if trigger_direction > 0 {
                            value = value.max(sign_eps);
                        }
                    }
                }
                state_values[prev_t_id] = time;
                state_values[prev_x_id] = value;
            }
            BODY_STMT_DDT => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let init_id = stmt.target_id.checked_add(1).ok_or(-2260)?;
                let last_t_id = stmt.target_id.checked_add(2).ok_or(-2261)?;
                let last_x_id = stmt.target_id.checked_add(3).ok_or(-2262)?;
                if last_x_id >= state_values.len() {
                    return Err(-2263);
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let x = pop1(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2264);
                }

                if state_values[init_id] == 0.0 {
                    state_values[stmt.target_id] = 0.0;
                    state_values[init_id] = 1.0;
                    state_values[last_t_id] = time;
                    state_values[last_x_id] = x;
                    pc += 1;
                    continue;
                }
                let dt = time - state_values[last_t_id];
                if dt > 0.0 {
                    let y = (x - state_values[last_x_id]) / dt;
                    state_values[stmt.target_id] = y;
                    state_values[last_t_id] = time;
                    state_values[last_x_id] = x;
                }
            }
            BODY_STMT_IDT => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let init_id = stmt.target_id.checked_add(1).ok_or(-2270)?;
                let last_t_id = stmt.target_id.checked_add(2).ok_or(-2271)?;
                let last_x_id = stmt.target_id.checked_add(3).ok_or(-2272)?;
                let last_eval_t_id = stmt.target_id.checked_add(4).ok_or(-2273)?;
                if last_eval_t_id >= state_values.len() {
                    return Err(-2274);
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let (x, ic) = pop2(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2275);
                }

                if state_values[init_id] == 0.0 {
                    state_values[stmt.target_id] = ic;
                    state_values[init_id] = 1.0;
                    state_values[last_t_id] = time;
                    state_values[last_x_id] = x;
                    state_values[last_eval_t_id] = time;
                    pc += 1;
                    continue;
                }
                if time != state_values[last_eval_t_id] {
                    let dt = time - state_values[last_t_id];
                    if dt > 0.0 {
                        state_values[stmt.target_id] += 0.5 * (x + state_values[last_x_id]) * dt;
                        state_values[last_t_id] = time;
                        state_values[last_x_id] = x;
                    } else if dt < 0.0 {
                        state_values[stmt.target_id] = ic;
                        state_values[last_t_id] = time;
                        state_values[last_x_id] = x;
                    }
                    state_values[last_eval_t_id] = time;
                }
            }
            BODY_STMT_IDTMOD => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let init_id = stmt.target_id.checked_add(1).ok_or(-2290)?;
                let last_t_id = stmt.target_id.checked_add(2).ok_or(-2291)?;
                let last_x_id = stmt.target_id.checked_add(3).ok_or(-2292)?;
                let last_eval_t_id = stmt.target_id.checked_add(4).ok_or(-2293)?;
                if last_eval_t_id >= state_values.len() {
                    return Err(-2294);
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let (x, ic, modulus) = pop3(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2295);
                }

                if state_values[init_id] == 0.0 {
                    let mut y = ic;
                    if modulus != 0.0 {
                        y = y.rem_euclid(modulus.abs());
                    }
                    state_values[stmt.target_id] = y;
                    state_values[init_id] = 1.0;
                    state_values[last_t_id] = time;
                    state_values[last_x_id] = x;
                    state_values[last_eval_t_id] = time;
                    pc += 1;
                    continue;
                }
                if time != state_values[last_eval_t_id] {
                    let dt = time - state_values[last_t_id];
                    if dt > 0.0 {
                        let mut y =
                            state_values[stmt.target_id] + 0.5 * (x + state_values[last_x_id]) * dt;
                        if modulus != 0.0 {
                            y = y.rem_euclid(modulus.abs());
                        }
                        state_values[stmt.target_id] = y;
                        state_values[last_t_id] = time;
                        state_values[last_x_id] = x;
                    } else if dt < 0.0 {
                        state_values[last_t_id] = time;
                        state_values[last_x_id] = x;
                    }
                    state_values[last_eval_t_id] = time;
                }
            }
            BODY_STMT_BOUND_STEP => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let value = stack.pop().ok_or(-2208)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                if capture_bound_step && value.is_finite() && value > 0.0 {
                    if !bound_step_limit.is_finite() || value < *bound_step_limit {
                        *bound_step_limit = value;
                    }
                }
            }
            BODY_STMT_FILE_OPEN => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                if stmt.target_id >= state_values.len() {
                    return Err(-2211);
                }
                if let Some(runtime) = file_io.as_deref_mut() {
                    if let Some(handle_id) = runtime.fopen(stmt.expr_start)? {
                        state_values[stmt.target_id] = handle_id;
                        pc += 1;
                        continue;
                    }
                }
                let handle_id = stmt.expr_start.checked_add(1).ok_or(-2223)?;
                state_values[stmt.target_id] = handle_id as f64;
                if let Some(log) = side_effect_log.as_deref_mut() {
                    log.push(BODY_STMT_FILE_OPEN, stmt.expr_start, time, &[])?;
                }
            }
            BODY_STMT_FILE_SCANF => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let spec_id = pop1(&mut stack)?;
                let fd = pop1(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                let Some(runtime) = file_io.as_deref_mut() else {
                    return Err(-2360);
                };
                let count = runtime.fscanf(
                    to_veriloga_integer_trunc(spec_id) as usize,
                    fd,
                    state_values,
                )?;
                if stmt.target_id < state_values.len() {
                    state_values[stmt.target_id] = if stmt.target_integer != 0 {
                        to_veriloga_integer(count) as f64
                    } else {
                        count
                    };
                }
            }
            BODY_STMT_FILE_GETS => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let _spec_id = pop1(&mut stack)?;
                let fd = pop1(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                let Some(runtime) = file_io.as_deref_mut() else {
                    return Err(-2361);
                };
                let status = runtime.fgets_discard(fd);
                if stmt.target_id < state_values.len() {
                    state_values[stmt.target_id] = status;
                }
            }
            BODY_STMT_FILE_TELL => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let op_code = pop1(&mut stack)?;
                let fd = pop1(&mut stack)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                let Some(runtime) = file_io.as_deref_mut() else {
                    return Err(-2362);
                };
                let mut value = if to_veriloga_integer_trunc(op_code) == 1 {
                    runtime.feof(fd)
                } else {
                    runtime.ftell(fd)
                };
                if stmt.target_integer != 0 {
                    value = to_veriloga_integer(value) as f64;
                }
                if stmt.target_id < state_values.len() {
                    state_values[stmt.target_id] = value;
                }
            }
            BODY_STMT_FILE_SEEK => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let op_code = pop1(&mut stack)?;
                let Some(runtime) = file_io.as_deref_mut() else {
                    return Err(-2363);
                };
                let status = if to_veriloga_integer_trunc(op_code) == 2 {
                    let fd = pop1(&mut stack)?;
                    runtime.fseek(fd, 0.0, 0.0)
                } else {
                    let whence = pop1(&mut stack)?;
                    let offset = pop1(&mut stack)?;
                    let fd = pop1(&mut stack)?;
                    runtime.fseek(fd, offset, whence)
                };
                if !stack.is_empty() {
                    return Err(-2209);
                }
                if stmt.target_id < state_values.len() {
                    let mut value = status;
                    if stmt.target_integer != 0 {
                        value = to_veriloga_integer(value) as f64;
                    }
                    state_values[stmt.target_id] = value;
                }
            }
            BODY_STMT_FILE_CLOSE => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let fd = *stack.first().unwrap_or(&0.0);
                let closed_runtime_handle = file_io
                    .as_deref_mut()
                    .map(|runtime| runtime.fclose(fd))
                    .unwrap_or(false);
                if !closed_runtime_handle {
                    if let Some(log) = side_effect_log.as_deref_mut() {
                        log.push(stmt.target_kind, stmt.target_id, time, &stack)?;
                    }
                }
                stack.clear();
            }
            BODY_STMT_FILE_WRITE | BODY_STMT_STROBE | BODY_STMT_STRING_WRITE => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                if let Some(log) = side_effect_log.as_deref_mut() {
                    log.push(stmt.target_kind, stmt.target_id, time, &stack)?;
                }
                stack.clear();
            }
            TARGET_NODE => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let value = stack.pop().ok_or(-2208)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                if stmt.target_id >= node_values.len() {
                    return Err(-2210);
                }
                node_values[stmt.target_id] = value;
            }
            TARGET_STATE => {
                if !branch_active_stack.iter().all(|flag| *flag != 0) {
                    pc += 1;
                    continue;
                }
                let expr_end = stmt.expr_start.checked_add(stmt.expr_count).ok_or(-2206)?;
                if expr_end > expr_ops.len() {
                    return Err(-2207);
                }
                stack.clear();
                evaluate_body_expr_segment(
                    &expr_ops[stmt.expr_start..expr_end],
                    node_values,
                    state_values,
                    param_values,
                    time,
                    &mut stack,
                )?;
                let mut value = stack.pop().ok_or(-2208)?;
                if !stack.is_empty() {
                    return Err(-2209);
                }
                if stmt.target_id >= state_values.len() {
                    return Err(-2211);
                }
                if stmt.target_integer != 0 {
                    value = to_veriloga_integer(value) as f64;
                }
                state_values[stmt.target_id] = value;
            }
            _ => return Err(-2212),
        }
        pc += 1;
    }
    if !branch_active_stack.is_empty() {
        return Err(-2215);
    }
    if !loop_stack.is_empty() {
        return Err(-2237);
    }
    Ok(())
}

fn find_matching_endwhile(stmt_ops: &[EvasRustBodyStmtOp], while_pc: usize) -> Result<usize, i32> {
    let mut depth = 0_usize;
    let mut pc = while_pc.checked_add(1).ok_or(-2231)?;
    while pc < stmt_ops.len() {
        match stmt_ops[pc].target_kind {
            BODY_STMT_WHILE => {
                depth = depth.checked_add(1).ok_or(-2238)?;
            }
            BODY_STMT_ENDWHILE => {
                if depth == 0 {
                    return Ok(pc);
                }
                depth -= 1;
            }
            _ => {}
        }
        pc += 1;
    }
    Err(-2239)
}

pub fn evaluate_body_expr_ops(
    expr_ops: &[EvasRustBodyExprOp],
    node_values: &[f64],
    state_values: &[f64],
    param_values: &[f64],
) -> Result<f64, i32> {
    evaluate_body_expr_ops_at_time(expr_ops, node_values, state_values, param_values, 0.0)
}

pub fn evaluate_body_expr_ops_at_time(
    expr_ops: &[EvasRustBodyExprOp],
    node_values: &[f64],
    state_values: &[f64],
    param_values: &[f64],
    time: f64,
) -> Result<f64, i32> {
    let mut stack: Vec<f64> = Vec::with_capacity(32);
    evaluate_body_expr_segment(
        expr_ops,
        node_values,
        state_values,
        param_values,
        time,
        &mut stack,
    )?;
    let value = stack.pop().ok_or(-2260)?;
    if !stack.is_empty() {
        return Err(-2261);
    }
    Ok(value)
}

pub fn evaluate_body_expr_segments(
    expr_ops: &[EvasRustBodyExprOp],
    expr_starts: &[usize],
    expr_counts: &[usize],
    node_values: &[f64],
    state_values: &[f64],
    param_values: &[f64],
    output_values: &mut [f64],
) -> Result<(), i32> {
    evaluate_body_expr_segments_at_time(
        expr_ops,
        expr_starts,
        expr_counts,
        node_values,
        state_values,
        param_values,
        output_values,
        0.0,
    )
}

pub fn evaluate_body_expr_segments_at_time(
    expr_ops: &[EvasRustBodyExprOp],
    expr_starts: &[usize],
    expr_counts: &[usize],
    node_values: &[f64],
    state_values: &[f64],
    param_values: &[f64],
    output_values: &mut [f64],
    time: f64,
) -> Result<(), i32> {
    if expr_starts.len() != expr_counts.len() || expr_starts.len() != output_values.len() {
        return Err(-2270);
    }

    let mut stack: Vec<f64> = Vec::with_capacity(32);
    for idx in 0..expr_starts.len() {
        let expr_end = expr_starts[idx]
            .checked_add(expr_counts[idx])
            .ok_or(-2271)?;
        if expr_end > expr_ops.len() {
            return Err(-2272);
        }
        stack.clear();
        evaluate_body_expr_segment(
            &expr_ops[expr_starts[idx]..expr_end],
            node_values,
            state_values,
            param_values,
            time,
            &mut stack,
        )?;
        output_values[idx] = stack.pop().ok_or(-2273)?;
        if !stack.is_empty() {
            return Err(-2274);
        }
    }
    Ok(())
}

pub fn evaluate_transition_target_ops(
    ops: &[EvasRustTransitionTargetOp],
    terms: &[EvasRustLinearTerm],
    conditions: &[EvasRustLinearCondition],
    node_values: &[f64],
    state_values: &[f64],
    target_values: &mut [f64],
    delay_values: &mut [f64],
    rise_values: &mut [f64],
    fall_values: &mut [f64],
) -> Result<(), i32> {
    for op in ops {
        if op.target_id >= target_values.len()
            || op.target_id >= delay_values.len()
            || op.target_id >= rise_values.len()
            || op.target_id >= fall_values.len()
        {
            return Err(-61);
        }
        let term_end = op.term_start.checked_add(op.term_count).ok_or(-62)?;
        if term_end > terms.len() {
            return Err(-63);
        }

        let mut value = evaluate_linear_value(
            op.bias,
            &terms[op.term_start..term_end],
            node_values,
            state_values,
        )?;
        if op.condition_id != CONDITION_NONE {
            if op.condition_id >= conditions.len() {
                return Err(-64);
            }
            let condition = &conditions[op.condition_id];
            if !evaluate_condition(condition, terms, node_values, state_values)? {
                let false_term_end = op
                    .false_term_start
                    .checked_add(op.false_term_count)
                    .ok_or(-65)?;
                if false_term_end > terms.len() {
                    return Err(-66);
                }
                value = evaluate_linear_value(
                    op.false_bias,
                    &terms[op.false_term_start..false_term_end],
                    node_values,
                    state_values,
                )?;
            }
        }

        target_values[op.target_id] = value;
        delay_values[op.target_id] = op.delay;
        rise_values[op.target_id] = op.rise;
        fall_values[op.target_id] = op.fall;
    }
    Ok(())
}

pub fn evaluate_ordered_transition_segment(
    linear_ops: &[EvasRustLinearOp],
    linear_terms: &[EvasRustLinearTerm],
    linear_conditions: &[EvasRustLinearCondition],
    transition_ops: &[EvasRustTransitionTargetOp],
    transition_terms: &[EvasRustLinearTerm],
    transition_conditions: &[EvasRustLinearCondition],
    node_values: &mut [f64],
    state_values: &mut [f64],
    target_values: &mut [f64],
    delay_values: &mut [f64],
    rise_values: &mut [f64],
    fall_values: &mut [f64],
) -> Result<(), i32> {
    evaluate_static_linear_ops(
        linear_ops,
        linear_terms,
        linear_conditions,
        node_values,
        state_values,
    )?;
    evaluate_transition_target_ops(
        transition_ops,
        transition_terms,
        transition_conditions,
        node_values,
        state_values,
        target_values,
        delay_values,
        rise_values,
        fall_values,
    )
}

pub fn timer_static_linear_trace_for_arrays(
    times: &[f64],
    source_node_ids: &[usize],
    source_values: &[f64],
    node_values: &mut [f64],
    state_values: &mut [f64],
    event_ops: &[EvasRustLinearOp],
    event_terms: &[EvasRustLinearTerm],
    event_conditions: &[EvasRustLinearCondition],
    evaluate_ops: &[EvasRustLinearOp],
    evaluate_terms: &[EvasRustLinearTerm],
    evaluate_conditions: &[EvasRustLinearCondition],
    record_node_ids: &[usize],
    out_values: &mut [f64],
    timer_start: f64,
    timer_period: f64,
    has_start: bool,
    eps: f64,
) -> Result<usize, i32> {
    let point_count = times.len();
    let source_count = source_node_ids.len();
    let record_count = record_node_ids.len();
    if source_values.len() != point_count.checked_mul(source_count).ok_or(-701)? {
        return Err(-702);
    }
    if out_values.len() != point_count.checked_mul(record_count).ok_or(-703)? {
        return Err(-704);
    }
    if timer_period <= 0.0 || !timer_period.is_finite() {
        return Err(-705);
    }
    if has_start && !timer_start.is_finite() {
        return Err(-706);
    }
    for id in source_node_ids {
        if *id >= node_values.len() {
            return Err(-707);
        }
    }
    for id in record_node_ids {
        if *id >= node_values.len() {
            return Err(-708);
        }
    }

    let tolerance = eps.abs();
    let mut next_fire = if has_start { timer_start } else { timer_period };
    if !next_fire.is_finite() {
        next_fire = timer_period;
    }
    let mut event_count = 0usize;
    let mut previous_time = f64::NEG_INFINITY;

    for point_idx in 0..point_count {
        let time = times[point_idx];
        if !time.is_finite() {
            return Err(-709);
        }
        if time + tolerance < previous_time {
            return Err(-710);
        }

        while time >= next_fire - tolerance {
            evaluate_static_linear_ops(
                event_ops,
                event_terms,
                event_conditions,
                node_values,
                state_values,
            )?;
            event_count = event_count.checked_add(1).ok_or(-711)?;
            next_fire += timer_period;
            if !next_fire.is_finite() {
                return Err(-712);
            }
        }

        let source_offset = point_idx.checked_mul(source_count).ok_or(-713)?;
        for source_idx in 0..source_count {
            node_values[source_node_ids[source_idx]] = source_values[source_offset + source_idx];
        }

        evaluate_static_linear_ops(
            evaluate_ops,
            evaluate_terms,
            evaluate_conditions,
            node_values,
            state_values,
        )?;

        let out_offset = point_idx.checked_mul(record_count).ok_or(-714)?;
        for record_idx in 0..record_count {
            out_values[out_offset + record_idx] = node_values[record_node_ids[record_idx]];
        }
        previous_time = time;
    }
    Ok(event_count)
}

pub fn timer_static_linear_queue_trace_for_arrays(
    times: &[f64],
    source_node_ids: &[usize],
    source_values: &[f64],
    node_values: &mut [f64],
    state_values: &mut [f64],
    timer_starts: &[f64],
    timer_periods: &[f64],
    event_op_starts: &[usize],
    event_op_counts: &[usize],
    event_ops: &[EvasRustLinearOp],
    event_terms: &[EvasRustLinearTerm],
    event_conditions: &[EvasRustLinearCondition],
    evaluate_ops: &[EvasRustLinearOp],
    evaluate_terms: &[EvasRustLinearTerm],
    evaluate_conditions: &[EvasRustLinearCondition],
    record_node_ids: &[usize],
    out_values: &mut [f64],
    eps: f64,
) -> Result<usize, i32> {
    let point_count = times.len();
    let source_count = source_node_ids.len();
    let record_count = record_node_ids.len();
    let timer_count = timer_starts.len();
    if timer_count == 0 {
        return Err(-741);
    }
    if timer_periods.len() != timer_count
        || event_op_starts.len() != timer_count
        || event_op_counts.len() != timer_count
    {
        return Err(-742);
    }
    if source_values.len() != point_count.checked_mul(source_count).ok_or(-743)? {
        return Err(-744);
    }
    if out_values.len() != point_count.checked_mul(record_count).ok_or(-745)? {
        return Err(-746);
    }
    for id in source_node_ids {
        if *id >= node_values.len() {
            return Err(-747);
        }
    }
    for id in record_node_ids {
        if *id >= node_values.len() {
            return Err(-748);
        }
    }
    for idx in 0..timer_count {
        if !timer_starts[idx].is_finite() || timer_starts[idx] < -eps.abs() {
            return Err(-749);
        }
        if timer_periods[idx] <= 0.0 || !timer_periods[idx].is_finite() {
            return Err(-750);
        }
        let op_end = event_op_starts[idx]
            .checked_add(event_op_counts[idx])
            .ok_or(-751)?;
        if op_end > event_ops.len() {
            return Err(-752);
        }
    }

    let tolerance = eps.abs();
    let mut next_fires: Vec<f64> = timer_starts
        .iter()
        .map(|start| if *start < 0.0 { 0.0 } else { *start })
        .collect();
    let mut event_count = 0usize;
    let mut previous_time = f64::NEG_INFINITY;

    for point_idx in 0..point_count {
        let time = times[point_idx];
        if !time.is_finite() {
            return Err(-753);
        }
        if time + tolerance < previous_time {
            return Err(-754);
        }

        loop {
            let mut next_due_time = f64::INFINITY;
            for fire_time in &next_fires {
                if *fire_time <= time + tolerance && *fire_time < next_due_time {
                    next_due_time = *fire_time;
                }
            }
            if !next_due_time.is_finite() {
                break;
            }

            for timer_idx in 0..timer_count {
                if next_fires[timer_idx] <= next_due_time + tolerance
                    && next_fires[timer_idx] <= time + tolerance
                {
                    let op_start = event_op_starts[timer_idx];
                    let op_end = op_start + event_op_counts[timer_idx];
                    evaluate_static_linear_ops(
                        &event_ops[op_start..op_end],
                        event_terms,
                        event_conditions,
                        node_values,
                        state_values,
                    )?;
                    event_count = event_count.checked_add(1).ok_or(-755)?;
                    next_fires[timer_idx] += timer_periods[timer_idx];
                    if !next_fires[timer_idx].is_finite() {
                        return Err(-756);
                    }
                }
            }
        }

        let source_offset = point_idx.checked_mul(source_count).ok_or(-757)?;
        for source_idx in 0..source_count {
            node_values[source_node_ids[source_idx]] = source_values[source_offset + source_idx];
        }

        evaluate_static_linear_ops(
            evaluate_ops,
            evaluate_terms,
            evaluate_conditions,
            node_values,
            state_values,
        )?;

        let out_offset = point_idx.checked_mul(record_count).ok_or(-758)?;
        for record_idx in 0..record_count {
            out_values[out_offset + record_idx] = node_values[record_node_ids[record_idx]];
        }
        previous_time = time;
    }
    Ok(event_count)
}

pub fn event_lfsr_shift_xor_step(
    state_values: &mut [f64],
    node_values: &[f64],
    lfsr_slots: &[usize],
    tmp_slots: &[usize],
    tap_slots: &[usize],
    gate_node_id: usize,
    gate_threshold: f64,
    high_node_id: usize,
    low_node_id: usize,
    output_state_id: usize,
    loop_state_id: usize,
    loop_final_value: f64,
) -> Result<bool, i32> {
    if lfsr_slots.is_empty() {
        return Err(-201);
    }
    if tmp_slots.len() < lfsr_slots.len() + 1 {
        return Err(-202);
    }
    for slot in lfsr_slots.iter().chain(tmp_slots).chain(tap_slots) {
        if *slot >= state_values.len() {
            return Err(-203);
        }
    }
    if output_state_id != CONDITION_NONE && output_state_id >= state_values.len() {
        return Err(-204);
    }
    if loop_state_id != CONDITION_NONE && loop_state_id >= state_values.len() {
        return Err(-205);
    }
    if gate_node_id != CONDITION_NONE {
        if gate_node_id >= node_values.len() {
            return Err(-206);
        }
        if node_values[gate_node_id] <= gate_threshold {
            return Ok(false);
        }
    }
    if output_state_id != CONDITION_NONE {
        if high_node_id >= node_values.len() || low_node_id >= node_values.len() {
            return Err(-207);
        }
    }

    let old_bits: Vec<i64> = lfsr_slots
        .iter()
        .map(|slot| to_veriloga_integer(state_values[*slot]) & 1)
        .collect();
    let mut feedback: i64 = 0;
    for slot in tap_slots {
        feedback ^= to_veriloga_integer(state_values[*slot]) & 1;
    }

    state_values[tmp_slots[0]] = feedback as f64;
    for (idx, bit) in old_bits.iter().enumerate() {
        state_values[tmp_slots[idx + 1]] = *bit as f64;
    }
    state_values[lfsr_slots[0]] = feedback as f64;
    for idx in 1..lfsr_slots.len() {
        state_values[lfsr_slots[idx]] = old_bits[idx - 1] as f64;
    }

    if output_state_id != CONDITION_NONE {
        let last_bit = to_veriloga_integer(state_values[*lfsr_slots.last().unwrap()]) & 1;
        state_values[output_state_id] = if last_bit > 0 {
            node_values[high_node_id]
        } else {
            node_values[low_node_id]
        };
    }
    if loop_state_id != CONDITION_NONE {
        state_values[loop_state_id] = to_veriloga_integer(loop_final_value) as f64;
    }
    Ok(true)
}

pub fn timer_lfsr_output_step_for_arrays(
    state_values: &mut [f64],
    node_values: &mut [f64],
    next_fire_times: &mut [f64],
    has_state_flags: &mut [u8],
    period: f64,
    start: f64,
    has_start: u8,
    time: f64,
    eps: f64,
    lfsr_slots: &[usize],
    tmp_slots: &[usize],
    tap_slots: &[usize],
    gate_node_id: usize,
    gate_threshold: f64,
    high_node_id: usize,
    low_node_id: usize,
    output_state_id: usize,
    output_node_id: usize,
    loop_state_id: usize,
    loop_final_value: f64,
    due: &mut u8,
    skipped: &mut u8,
    executed: &mut u8,
    output_written: &mut u8,
) -> Result<(), i32> {
    if next_fire_times.len() != 1 || has_state_flags.len() != 1 {
        return Err(-251);
    }
    *due = 0;
    *skipped = 0;
    *executed = 0;
    *output_written = 0;

    let periods = [period];
    let starts = [start];
    let has_starts = [has_start];
    let mut due_flags = [0_u8];
    let mut skipped_flags = [0_u8];
    timer_periodic_step_for_arrays(
        next_fire_times,
        has_state_flags,
        &periods,
        &starts,
        &has_starts,
        &mut due_flags,
        &mut skipped_flags,
        time,
        true,
        eps,
    )?;
    *due = due_flags[0];
    *skipped = skipped_flags[0];
    if due_flags[0] == 0 {
        return Ok(());
    }

    let did_execute = event_lfsr_shift_xor_step(
        state_values,
        node_values,
        lfsr_slots,
        tmp_slots,
        tap_slots,
        gate_node_id,
        gate_threshold,
        high_node_id,
        low_node_id,
        output_state_id,
        loop_state_id,
        loop_final_value,
    )?;
    if !did_execute {
        return Ok(());
    }
    *executed = 1;
    if output_node_id != CONDITION_NONE && output_state_id != CONDITION_NONE {
        if output_node_id >= node_values.len() || output_state_id >= state_values.len() {
            return Err(-252);
        }
        node_values[output_node_id] = state_values[output_state_id];
        *output_written = 1;
    }
    Ok(())
}

pub fn dynamic_bus_offsets_for_arrays(
    base_offsets: &[usize],
    outer_lengths: &[usize],
    inner_strides: &[usize],
    inner_lengths: &[usize],
    first_indices: &[i64],
    second_indices: &[i64],
    has_second_index_flags: &[u8],
    out_node_ids: &mut [usize],
) -> Result<(), i32> {
    let count = base_offsets.len();
    if outer_lengths.len() != count
        || inner_strides.len() != count
        || inner_lengths.len() != count
        || first_indices.len() != count
        || second_indices.len() != count
        || has_second_index_flags.len() != count
        || out_node_ids.len() != count
    {
        return Err(-201);
    }

    for idx in 0..count {
        let first = first_indices[idx];
        if first < 0 || outer_lengths[idx] == 0 || first as usize >= outer_lengths[idx] {
            return Err(-202);
        }
        let mut offset = (first as usize)
            .checked_mul(inner_strides[idx])
            .ok_or(-203)?;
        if has_second_index_flags[idx] != 0 {
            let second = second_indices[idx];
            if second < 0 || inner_lengths[idx] == 0 || second as usize >= inner_lengths[idx] {
                return Err(-204);
            }
            offset = offset.checked_add(second as usize).ok_or(-205)?;
        }
        out_node_ids[idx] = base_offsets[idx].checked_add(offset).ok_or(-206)?;
    }
    Ok(())
}
