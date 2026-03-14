# EVAS — Event-driven Verilog-A Simulator

[![PyPI version](https://img.shields.io/pypi/v/evas-sim.svg)](https://pypi.org/project/evas-sim/)
[![CI](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml/badge.svg)](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-evas.tokenzhang.com-blue)](https://evas.tokenzhang.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

EVAS is a lightweight, pure-Python behavioral simulator for digital/control-class
Verilog-A models. It provides fast event-driven simulation with zero external
EDA dependencies — no C compiler, no ngspice.

📖 **Documentation:** [evas.tokenzhang.com](https://evas.tokenzhang.com) | [English](/en/)

## Target Use Cases

- Rapid behavioral verification of clocked digital blocks (comparators, ADCs, DACs, dividers, LFSRs, DFFs)
- Event-driven models using `@cross`, `@above`, `transition()`, `@initial_step`
- Voltage-mode contributions (`V() <+`)

## Installation

```bash
pip install evas-sim
```

No C compiler, no ngspice. NumPy, Matplotlib, and Pandas are installed automatically.

## Quickstart

```bash
# List all 15 bundled examples
evas list

# Run the clock divider example
evas run clk_div

# Run a specific testbench in a multi-testbench example
evas run digital_basics --tb tb_not_gate.scs

# Simulate your own netlist
evas simulate path/to/tb.scs -o output/mydesign
```

`evas run clk_div` copies the Verilog-A model and Spectre testbench into
`./clk_div/`, simulates it, and saves waveform data + a PNG plot to
`./output/clk_div/`.

## Bundled Examples

| Name | Description |
|------|-------------|
| `clk_div` | Clock divider (ratio = 4) |
| `clk_burst_gen` | Clock burst generator |
| `digital_basics` | Basic gates: AND, NOT, OR, DFF, inverter chain |
| `lfsr` | Linear feedback shift register |
| `noise_gen` | Noise signal generator |
| `ramp_gen` | Ramp signal generator |
| `edge_interval_timer` | Edge-interval timer |
| `d2b_4b` | 4-bit thermometer-to-binary decoder |
| `dac_binary_clk_4b` | 4-bit binary DAC (clocked) |
| `dac_therm_16b` | 16-bit thermometer DAC |
| `adc_dac_ideal_4b` | 4-bit ideal ADC + DAC with sample-hold |
| `cmp_strongarm` | StrongARM comparator |
| `cmp_offset_search` | Comparator offset search algorithm |
| `dwa_ptr_gen` | DWA pointer generator |
| `sar_adc_dac_weighted_8b` | 8-bit weighted SAR ADC + DAC |

## CSV Output Format

All signals default to 6-digit scientific notation (`:.6e`). The `save` statement
accepts an optional `:fmt` suffix per signal:

| Suffix | Format | Example value |
|--------|--------|---------------|
| `:6e` | `:.6e` (default) | `4.500000e-01` |
| `:10e` | `:.10e` | `4.5000000000e-01` |
| `:2e` | `:.2e` | `4.50e-01` |
| `:4f` | `:.4f` | `0.4500` |
| `:d` | integer | `7` |

```
// With format hints
save vin:10e vout:6e clk:2e dout_code:d
```

## Supported Verilog-A Features

- Module declarations with parameters and port arrays
- `@(cross(...))`, `@(above(...))` zero-crossing events
- `@(initial_step)` initialization; combined events
- `transition()` operator with delay, rise/fall times
- `V(node)`, `V(a, b)` voltage access; `V(node) <+` voltage contributions
- Arithmetic, logical, bitwise, shift, ternary operators
- `for` loops, `if/else`, `begin/end` blocks
- Integer and real variables, arrays, parameters with ranges
- `` `include ``, `` `define ``, `` `default_transition `` preprocessor directives
- SI suffixes; math functions: `ln`, `log`, `exp`, `sqrt`, `pow`, `abs`, `sin`, `cos`, `floor`, `ceil`, `min`, `max`
- String parameters with `.substr()` method calls

## Spectre Netlist Support

- `vsource` with `dc`, `pulse`, `pwl`, `sin` types
- `ahdl_include` for VA model files
- `parameters` with expression evaluation
- `tran` analysis with `stop` and `maxstep`
- `save` signal selection with optional per-signal format specifiers

## Limitations

- No `I() <+` current contributions
- No `ddt()`, `idt()` calculus operators
- No MNA matrix solve (no KCL/KVL enforcement)
- No transistor-level simulation
- No AC or DC analysis (transient only)
- No subcircuit hierarchy

## Project Structure

```
EVAS/
├── evas/
│   ├── cli.py               # CLI entry point (evas simulate / run / list)
│   ├── compiler/            # Verilog-A front-end (lexer, parser, AST)
│   ├── simulator/           # Event-driven simulation engine + backend
│   ├── netlist/             # Spectre .scs parser + orchestration runner
│   ├── vams/                # VAMS include files (constants, disciplines)
│   └── examples/            # 15 bundled example circuits
├── docs/                    # Sphinx documentation (Chinese, default)
├── docs_en/                 # Sphinx documentation (English)
├── tests/
└── pyproject.toml
```

## Development

```bash
git clone https://github.com/Arcadia-1/EVAS.git
cd EVAS
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
