# Examples Catalog

EVAS ships with **14 example groups**, each containing one or more Verilog-A model files (`.va`),
Spectre-format testbench netlists (`.scs`), and Python analysis / visualisation scripts.

Use `evas run <name>` to run any example.

## Available Examples

| Group | Variants / sub-examples |
|-------|------------------------|
| `clk_div` | Clock divider (ratio = 4) |
| `clk_burst_gen` | Clock burst generator |
| `digital_basics` | AND, OR, NOT gates; D flip-flop with reset; inverter chain |
| `lfsr` | Linear feedback shift register |
| `noise_gen` | Gaussian noise generator |
| `ramp_gen` | Ramp signal generator |
| `edge_interval_timer` | Edge-interval timer |
| `d2b_4b` | 4-bit thermometer-to-binary decoder |
| `dac_binary_clk_4b` | 4-bit binary DAC (clocked) |
| `dac_therm_16b` | 16-bit thermometer DAC |
| `adc_dac_ideal_4b` | 4-bit ideal ADC + DAC with sample-hold: a) ramp  b) single-tone sine  c) 1000-point sine |
| `comparator` | a) Ideal comparator  b) StrongARM clocked comparator  c) Binary-search offset calibration  d) Propagation delay measurement |
| `dwa_ptr_gen` | a) Overlap variant (code+1 cells/cycle)  b) No-overlap variant — both at 100 MHz via `v2b_4b` voltage-to-binary ADC |
| `sar_adc_dac_weighted_8b` | 8-bit binary-weighted SAR ADC + DAC; ramp input; DNL/INL characterisation |

## Running a specific sub-example

Use `--tb` to select a testbench when an example has multiple:

```bash
# adc_dac_ideal_4b stimuli
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_ramp.scs
evas run adc_dac_ideal_4b --tb tb_adc_dac_ideal_4b_sine.scs

# comparator sub-examples
evas run comparator --tb tb_cmp_ideal.scs
evas run comparator --tb tb_cmp_strongarm.scs
evas run comparator --tb tb_cmp_offset_search.scs
evas run comparator --tb tb_cmp_delay.scs

# digital_basics gates
evas run digital_basics --tb tb_and_gate.scs
evas run digital_basics --tb tb_not_gate.scs
```
