---
name: evas-sim
description: |
  How to use the EVAS Verilog-A behavioral simulator (pip package: evas-sim).
  Use this skill whenever the user wants to simulate a Verilog-A (.va) model,
  run a Spectre (.scs) netlist, check simulation feasibility, install evas-sim,
  or read simulation output (tran.csv, strobe.txt). Trigger on phrases like
  "simulate this", "run this VA model", "can EVAS handle this", "evas run",
  "evas simulate", "check if this is simulatable", or any mention of evas-sim.
---

# EVAS Simulation Skill

EVAS (Event-driven Verilog-A Simulator) is a pure-Python behavioral simulator
for **voltage-mode, event-driven** Verilog-A models. It has no analog solver —
no KCL/KVL, no MNA matrix. Before simulating anything, you must verify the
model is compatible.

---

## Step 0 — Pre-simulation Compatibility Check (MANDATORY)

**Always read the `.va` file(s) before attempting simulation.**

Scan for the following patterns. If ANY are found, **stop immediately** and
tell the user that EVAS cannot simulate this model:

| Pattern | Meaning | EVAS support |
|---------|---------|--------------|
| `I(...)  <+` | Current contribution | ❌ Not supported |
| `q(...)  <+` | Charge contribution | ❌ Not supported |
| `ddt(...)` | Time derivative | ❌ Not supported |
| `idt(...)` | Time integral | ❌ Not supported |
| `V(...) <+` with node equations | Voltage contribution | ✅ Supported |
| `@(cross(...))`, `@(above(...))` | Events | ✅ Supported |
| `@(initial_step)` | Init event | ✅ Supported |
| `transition(...)` | Waveform shaping | ✅ Supported |

**When to say NO:** If the model drives currents, models a transistor, uses
charge-based storage, or integrates/differentiates signals, EVAS is the wrong
tool. Suggest ngspice or Xyce instead and explain why.

**Good candidates for EVAS:**
- Clock generators, dividers, burst generators
- Digital control logic (counters, LFSRs, state machines)
- Simplified ADC/DAC behavioral models (voltage output, code input)
- Comparators modeled as threshold detectors (voltage-based decision)
- Signal measurement blocks (edge timers, interval counters)
- Noise/ramp/PWL generators

---

## Step 1 — Installation

Prefer `uv` for speed; fall back to `pip` if uv is not available.

```bash
# Preferred
uv pip install evas-sim

# Fallback
pip install evas-sim
```

Verify installation:
```bash
evas list
```

This should print the 15 bundled example names. If `evas` is not found,
the install succeeded but the script directory isn't on PATH — try
`python -m evas` or check the virtualenv activation.

---

## Step 2 — Simulate

### Option A: Simulate a custom netlist directly

```bash
evas simulate path/to/tb_mydesign.scs -o output/mydesign
```

Options:
- `-o / --output <dir>` — output directory (default: `./output`)
- `-log <file>` — write log to file in addition to stdout

### Option B: Run a bundled example

```bash
evas list                          # see all available examples
evas run clk_div                   # runs tb_clk_div.scs from the package
evas run digital_basics --tb tb_not_gate.scs   # pick a specific testbench
```

`evas run` copies the example files to `./evas-run/<name>/` and saves
output to `./evas-run/output/<name>/`. It does not pollute the project root.

---

## Step 3 — Read Simulation Output

All output lands in the directory specified by `-o` (or `evas-run/output/<name>/`).

### `tran.csv` — Waveform data

Time-domain CSV, one column per saved signal:

```
time,clk_in,clk_out,dout_code
0.000000e+00,0.000000e+00,0.000000e+00,0
1.000000e-09,9.000000e-01,0.000000e+00,0
...
```

- `time` column is in **seconds**
- Voltage columns are in **volts**
- Integer bus codes use `:d` format (no decimal point)

Read with pandas:
```python
import pandas as pd
df = pd.read_csv("output/mydesign/tran.csv")
t_ns = df["time"].values * 1e9   # convert to ns
```

### `strobe.txt` — `$strobe` / `$display` log

All `$strobe` and `$display` calls, in time order:
```
[clk_div] INIT | ratio=4 | vdd=0.9V
[clk_div] t=6.000 ns | count=1 | n=0
```

Useful for checking state machine transitions, parameter values at init,
and cycle-by-cycle counters.

### `tran.png` — Auto-generated waveform plot

A multi-panel plot of all saved signals. Generated automatically; open it
to do a quick visual sanity check before diving into the CSV.

---

## Common Issues

| Symptom | Likely cause |
|---------|--------------|
| `evas: command not found` | PATH issue — activate virtualenv or use `python -m evas` |
| Empty `tran.csv` | No `save` statement in `.scs` — add `save sig1 sig2 ...` |
| All voltages are 0 | Model uses `I() <+` (not supported) — EVAS voltage stays 0 |
| `Compiled Verilog-A module` not printed | Parse error — check `ahdl_include` path in `.scs` |
