# EVAS Examples

Each subdirectory contains one Verilog-A module (`<name>.va`) and, where applicable, a Spectre testbench (`tb_<name>.scs`).

## Runnable examples (with testbench)

| Module | Description |
|--------|-------------|
| `sar_adc_4b` | 4-bit SAR ADC. Clocked. Full sweep testbench from GND to VDD. |
| `sar_adc_weighted` | 12-bit SAR ADC with non-binary weights (501,270,144…0.5). Matches `dac_weighted_11b`. |
| `dac_binary_clk_4b` | 4-bit binary-weighted DAC. Clocked (CLK input). Separate DIN pins. |
| `dac_weighted_11b` | 11-bit DAC with non-uniform weights (SAR calibration DAC). RDY-triggered. |
| `cmp_strongarm` | Clocked StrongARM-style comparator. Differential input, differential output. |
| `digital_basics` | Basic digital building blocks: `and_gate`, `or_gate`, `not_gate`, `dff_rst` (D flip-flop with synchronous reset). 1.2 V logic. Each module has its own testbench. |
| `lfsr` | Linear Feedback Shift Register with enable and reset. |

## ADC / DAC models

| Module | Description |
|--------|-------------|
| `adc_ideal_8b` | Ideal 8-bit ADC. Combinational. Direct division, no clock. |
| `dac_binary_8b` | 8-bit binary DAC. Combinational (no clock). |
| `dac_therm_16b` | 16-bit thermometer-coded DAC. Static (no clock). |
| `dac_onehot_inv_16b` | 16-bit one-cold DAC (inverted one-hot). Static. |
| `dac_therm_inv_16b` | 16-bit thermometer DAC, inverted output. Static. |

## Digital bus drivers (static, no clock)

These drive multi-bit `electrical` buses from integer parameters. Used as stimulus sources in testbenches.

| Module | Description |
|--------|-------------|
| `d2b_4b` | Unified static code driver. From a single `trim_code` parameter, drives six buses: binary (active-high/low), one-hot (active-high/low), and thermometer (active-high/low). |

## Calibration circuits

| Module | Description |
|--------|-------------|
| `cmp_offset_search` | Binary search for comparator input offset. Drives differential VINP/VINN around VINCM. |

## DTC (Digital-to-Time Converter) related

| Module | Description |
|--------|-------------|
| `edge_interval_timer` | Measures time interval between rising edges of CLK_1 and CLK_2. Output in picoseconds. |
| `clk_burst_gen` | Outputs two consecutive CLK pulses every `div` input clock cycles. Waveform generator for DTC characterization. |

## Stimulus / utility

| Module | Description |
|--------|-------------|
| `clk_div` | Clock divider. Output high for upper half of every `ratio` input cycles (50% duty cycle for even ratio). |
| `ramp_gen` | Generates a bidirectional integer ramp code (up/down). Configurable speed and limits. |
| `ramp_gen_transitions` | Steps through ADC transition points. Used for INL/DNL sweep. |
| `dwa_ptr_gen` | DWA (Data Weighted Averaging) pointer rotation generator. Configurable direction and interval. |
| `noise_gen` | Injects Gaussian-distributed noise onto a node. Configurable sigma. |
