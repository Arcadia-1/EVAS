# EVAS — Event-driven Verilog-A Simulator

[![PyPI](https://img.shields.io/pypi/v/evas-sim.svg)](https://pypi.org/project/evas-sim/)
[![CI](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml/badge.svg)](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-evas.tokenzhang.com-blue)](https://evas.tokenzhang.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight, pure-Python behavioral simulator for digital/mixed-signal Verilog-A models.
Event-driven. No C compiler, no ngspice, no KCL/KVL solver.

📖 **Docs:** [evas.tokenzhang.com](https://evas.tokenzhang.com) &nbsp;|&nbsp; [中文](https://evas.tokenzhang.com/zh/)

---

> **If you're a human** — install with pip and jump to [Quickstart](#quickstart).
>
> **If you're an AI agent** — copy `skills/evas-sim/` into your `.claude/skills/`
> (Claude Code) or equivalent skills directory for your agent framework.
> The skill file gives you built-in knowledge of EVAS commands, supported
> Verilog-A features, and common failure modes so you can simulate and
> debug without guessing.

---

## Installation

```bash
pip install evas-sim
```

Verify:

```bash
evas list        # prints all bundled examples
```

If `evas` is not on PATH, use `python -m evas`.

## Quickstart

```bash
# Run a bundled example
evas run clk_div

# Run with a specific testbench (for multi-TB examples)
evas run digital_basics --tb tb_not_gate.scs

# Simulate your own netlist
evas simulate path/to/tb.scs -o output/mydesign
```

Output lands in `./output/<name>/` (bundled) or the `-o` directory (custom).
Each run produces `tran.csv` (waveforms), one or more `.png` plots, and `strobe.txt` (log messages).

## Bundled Examples

| Name | Description |
|------|-------------|
| `clk_div` | Clock divider |
| `clk_burst_gen` | Clock burst generator |
| `lfsr` | Linear feedback shift register |
| `noise_gen` | Noise signal generator |
| `ramp_gen` | Ramp signal generator |
| `edge_interval_timer` | Edge-interval timer |
| `d2b_4b` | 4-bit thermometer-to-binary decoder |
| `dac_binary_clk_4b` | 4-bit binary DAC (clocked) |
| `dac_therm_16b` | 16-bit thermometer DAC |
| `adc_dac_ideal_4b` | 4-bit ideal ADC + DAC with sample-hold |
| `comparator` | StrongARM comparator + binary-search offset calibration |
| `dwa_ptr_gen` | DWA pointer generator — overlap variant (100 MHz, v2b_4b input) |
| `dwa_ptr_gen_no_overlap` | DWA pointer generator — no-overlap variant |
| `sar_adc_dac_weighted_8b` | 8-bit weighted SAR ADC + DAC |
| `digital_basics` | AND / OR / NOT gates, D flip-flop, inverter chain |

## Supported Verilog-A

| Feature | Status |
|---------|--------|
| `V(node) <+`, `V(a,b)` differential | ✅ |
| `@(cross(...))`, `@(above(...))`, `@(initial_step)` | ✅ |
| `@(timer(period))`, `@(final_step)` | ✅ |
| `transition()` with delay / rise / fall | ✅ |
| `for`, `if/else`, `case/endcase`, `begin/end` | ✅ |
| arrays, parameters, string parameters | ✅ |
| `` `include ``, `` `define ``, `` `default_transition `` | ✅ |
| SI suffixes, math functions (`sin`, `cos`, `exp`, `ln`, …) | ✅ |
| `$temperature`, `$vt`, `$abstime` | ✅ |
| `$bound_step()` | ✅ |
| `$fopen()`, `$fclose()`, `$fstrobe()`, `$fwrite()`, `$fdisplay()` | ✅ |
| `$display`, `$strobe`, `$random`, `$dist_uniform()`, `$rdist_normal()` | ✅ |
| `I() <+`, `ddt()`, `idt()`, `q() <+` | ❌ |
| AC/DC analysis, subcircuit hierarchy, transistors | ❌ |

## CSV Output Format

The `save` statement accepts optional per-signal format hints:

```
save vin:10e vout:6e clk:2e dout_code:d
```

| Suffix | Format |
|--------|--------|
| `:6e` (default) | `4.500000e-01` |
| `:Nf` | fixed-point, N decimal places |
| `:d` | integer |

## Development

```bash
git clone https://github.com/Arcadia-1/EVAS.git
cd EVAS
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
