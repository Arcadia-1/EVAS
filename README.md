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

14 example groups, 27 Verilog-A modules in total. Each group provides `.va` model files,
Spectre-format testbench netlists (`.scs`), and Python analysis / visualisation scripts.

| Group | Verilog-A modules | Notes |
|-------|------------------|-------|
| `clk_div` | `clk_div` | |
| `clk_burst_gen` | `clk_burst_gen` | |
| `digital_basics` | `and_gate`, `or_gate`, `not_gate`, `dff_rst`, `inverter` | |
| `lfsr` | `lfsr` | |
| `noise_gen` | `noise_gen` | |
| `ramp_gen` | `ramp_gen` | |
| `edge_interval_timer` | `edge_interval_timer` | also used inside `comparator` |
| `d2b_4b` | `d2b_4b` | thermometer-to-binary decoder |
| `dac_binary_clk_4b` | `dac_binary_clk_4b` | |
| `dac_therm_16b` | `dac_therm_16b` | |
| `adc_dac_ideal_4b` | `adc_ideal_4b`, `dac_ideal_4b`, `sh_ideal` | 3 stimuli: ramp / sine / 1000-pt sine |
| `comparator` | `cmp_ideal`, `cmp_strongarm`, `cmp_offset_search`, `cmp_delay` | 4 sub-examples |
| `dwa_ptr_gen` | `dwa_ptr_gen`, `dwa_ptr_gen_no_overlap`, `v2b_4b` | 100 MHz; `v2b_4b` = ideal voltage→4-bit ADC |
| `sar_adc_dac_weighted_8b` | `sar_adc_weighted_8b`, `dac_weighted_8b`, `sh_ideal` | 8-bit SAR; DNL/INL characterisation |

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

## Contributing

To develop or contribute to EVAS locally:

```bash
git clone https://github.com/Arcadia-1/EVAS.git
cd EVAS
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
