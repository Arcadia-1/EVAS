---
name: evas-sim
description: |
  How to use the EVAS Verilog-A behavioral simulator (pip package: evas-sim).
  Use this skill whenever the user wants to simulate a Verilog-A (.va) model,
  run a Spectre (.scs) netlist, check simulation feasibility, install evas-sim,
  or read simulation output (tran.csv, strobe.txt). Trigger on phrases like
  "simulate this", "run this VA model", "can EVAS handle this", "evas run",
  "evas simulate", "check if this is simulatable", or any mention of evas-sim.
license: MIT — see LICENSE.txt
evals: evals/evals.json
---

EVAS is a pure-Python, **voltage-mode, event-driven** Verilog-A simulator. No KCL/KVL, no analog solver.

## Compatibility check (do this first)

Read the `.va` file before simulating. If any unsupported pattern is found, stop and suggest ngspice or Xyce instead.

| Pattern | Support |
|---------|---------|
| `V(...) <+`, `@(cross(...))`, `@(above(...))`, `@(initial_step)`, `transition(...)` | ✅ |
| `I(...) <+`, `q(...) <+`, `ddt(...)`, `idt(...)` | ❌ |

## Install

```bash
uv pip install evas-sim   # preferred
pip install evas-sim      # fallback
evas list                 # verify: prints 15 bundled examples
```

If `evas` is not found after install, use `python -m evas` or check virtualenv activation.

## Simulate

```bash
# Custom netlist
evas simulate path/to/tb.scs -o output/mydesign

# Bundled example
evas run clk_div
evas run digital_basics --tb tb_not_gate.scs
```

Output goes to `-o` dir (default `./output`) or `./evas-run/output/<name>/` for `evas run`.

## Output files

| File | Contents |
|------|----------|
| `tran.csv` | Time-domain waveforms; `time` in seconds, voltages in volts, bus codes as integers |
| `strobe.txt` | `$strobe`/`$display` messages in time order |
| `tran.png` | Auto-generated multi-panel waveform plot |

## Common issues

| Symptom | Fix |
|---------|-----|
| `evas: command not found` | Activate virtualenv or use `python -m evas` |
| Empty `tran.csv` | Add `save sig1 sig2 ...` to the `.scs` netlist |
| All voltages are 0 | Model uses `I() <+` — not supported |
| `Compiled Verilog-A module` not printed | Parse error — check `ahdl_include` path in `.scs` |
