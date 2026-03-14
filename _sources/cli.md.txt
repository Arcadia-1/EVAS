# CLI Reference

EVAS provides three subcommands:

## `evas list`

Print all bundled example names.

```bash
evas list
```

## `evas run <name>`

Copy a bundled example to the current directory and simulate it.

```bash
evas run clk_div
evas run digital_basics
evas run sar_adc_dac_weighted_8b
```

Multi-testbench examples (e.g. `adc_dac_ideal_4b`, `digital_basics`) use
`tb_<name>.scs` by default. Use `--tb` to select a different testbench:

```bash
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine.scs
evas run digital_basics --tb tb_not_gate.scs
```

Output goes to `./output/<name>/`. Analysis plots (if an `analyze_<name>.py`
script is present) are saved there as well.

**Environment variable:** `EVAS_OUTPUT_DIR` is set automatically for the
analysis script so it can locate simulation results.

## `evas simulate <file.scs>`

Simulate an arbitrary Spectre netlist file directly.

```bash
evas simulate path/to/tb_mydesign.scs -o output/mydesign -log sim.log
```

| Option | Default | Description |
|--------|---------|-------------|
| `-o / --output` | `./output` | Output directory |
| `-log` | *(none)* | Path for a log file |

Exit code is `0` on success, `1` on simulation error.
