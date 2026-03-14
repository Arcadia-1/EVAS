# Examples Catalog

EVAS ships with 15 example circuits covering basic digital logic through
complex mixed-signal systems.

Use `evas run <name>` to run any example.

## Available Examples

| Name | Description |
|------|-------------|
| `clk_div` | Clock divider (ratio = 4) |
| `clk_burst_gen` | Clock burst generator |
| `digital_basics` | Basic gates: AND, NOT, OR, DFF with reset, inverter chain |
| `lfsr` | Linear feedback shift register |
| `noise_gen` | Noise signal generator |
| `ramp_gen` | Ramp signal generator |
| `edge_interval_timer` | Edge-interval timer |
| `d2b_4b` | 4-bit thermometer-to-binary decoder |
| `dac_binary_clk_4b` | 4-bit binary DAC (clocked) |
| `dac_therm_16b` | 16-bit thermometer DAC |
| `adc_dac_ideal_4b` | 4-bit ideal ADC + DAC with sample-hold (3 stimulus variants) |
| `cmp_strongarm` | StrongARM comparator |
| `cmp_offset_search` | Comparator offset search algorithm |
| `dwa_ptr_gen` | DWA pointer generator (data-weighted averaging) |
| `sar_adc_dac_weighted_8b` | 8-bit weighted SAR ADC + DAC |

## Multi-testbench Examples

`adc_dac_ideal_4b` and `digital_basics` contain multiple testbench files.
The default is `tb_<name>.scs`; use `--tb` to choose another:

```bash
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_ramp.scs
evas run digital_basics --tb tb_and_gate.scs
```
