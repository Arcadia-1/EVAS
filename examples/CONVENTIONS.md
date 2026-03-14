# EVAS Example Conventions

## 1. `$strobe` Logging Format

Every Verilog-A module must include `$strobe` statements so simulation output
can be parsed and validated by the companion `validate_*.py` scripts.

### General rules

- **Separator**: ` | ` (space-pipe-space) between fields. No trailing separator.
- **Module tag**: `[module_name]` at the start, matching the `module` declaration.
- The runner does **not** prepend a time prefix — each module owns its full line.

### INIT line — emitted once at `initial_step`

```
[module_name] INIT | key=val | key=val
```

```verilog
@(initial_step)
    $strobe("[module_name] INIT | vth=%.3gV | ratio=%d", vth, ratio);
```

### Event line — emitted on clock edges / discrete events

```
[module_name] t=%.3f ns | key=val | key=val
```

```verilog
@(cross(V(CLK) - vth, +1))
    $strobe("[module_name] t=%.3f ns | key=%d", $abstime*1e9, key);
```

### INIT/RST line — reset fired mid-simulation

```
[module_name] t=%.3f ns | INIT/RST | key=val
```

### Format specifiers

| Field type | Specifier | Example |
|---|---|---|
| Supply / threshold voltage | `%.3gV` | `0.9V`, `0.45V` |
| Signal voltage (precision) | `%.5gV` | `0.55406V` |
| Signal voltage (mV display) | `%.2fmV` | `1.23mV` |
| Time interval | `%.3f ps` | `123.456 ps` |
| Time parameter (ps) | `%.3gps` | `100ps` |
| Integer / code | `%d` | `7` |
| Binary word (N bits, MSB first) | `%d%d%d...` | `10011100` |
| Padded index | `%2d` | ` 3` |
| Small real parameter | `%g` | `1e-12` |

### Module reference

| Module | INIT fields | Event trigger | Event fields |
|---|---|---|---|
| `sar_adc_weighted_8b` | `vdd`, `total_sum` | CLK+ | `vin`, `code` (8-bit binary) |
| `dac_weighted_8b` | `vdd`, `total_sum` | — | — |
| `adc_ideal_4b` | `vdd_vss` | CLK+ | `vin`, `code` |
| `dac_ideal_4b` | `vdd_vss` | — | — |
| `sh_ideal` | `vdd_vss` | — | — |
| `dac_binary_clk_4b` | `vref`, `vth` | CLK+ | `aout` |
| `dac_therm_16b` | `vstep`, `vth` | — | — |
| `dff_rst` | `vth`, `vhigh` | CLK+ | `rst`, `d`, `q` |
| `clk_div` | `ratio`, `vdd` | CLK+ | `count`, `n` |
| `clk_burst_gen` | `div`, `vdd`, `vth` | CLK+ | `counter`, `gate` |
| `cmp_offset_search` | `vdd`, `step` | CLK+ | `sign`, `vin`, `step` |
| `cmp_strongarm` | — | CLK+ | `vinp`, `vinn`, `diff`, `diff_off`, `dec` |
| `dwa_ptr_gen` | — | CLK+ | `ptr`, `msb`, `lsb`, `cell` (16-bit), `ptr_bits` (16-bit) |
| `edge_interval_timer` | — | CLK_2+ | `delay`, `diff` |
| `lfsr` | `seed`, `DPN` (INIT/RST) | CLK+ | `DPN` |
| `ramp_gen` | `dir`, `code` | CLK+ | `code` (reset) / `cycle`, `code` |
| `inverter` | `td`, `tr`, `vhigh` | — | — |
| `and_gate` | `vth`, `vhigh` | — | — |
| `or_gate` | `vth`, `vhigh` | — | — |
| `not_gate` | `vth`, `vhigh` | — | — |
| `conf_word_gen` | `vth`, `trise`, `tfall` | — | — |
| `d2b_4b` | `trim_code`, `n` | — | — |
| `noise_gen` | `sigma` | — | — |
| `frame_generator` | `conf`, `n_conf_max` | — | — |

---

## 2.5 `save` Signal Format Conventions

The `save` statement accepts an optional `:fmt` suffix per signal.
All examples follow this scheme:

| Signal type | Suffix | Example | Rationale |
|-------------|--------|---------|-----------|
| Analog voltage (vin, vout, …) | `:3f` | `0.450` | 3 decimal places, human-readable |
| Clock / square-wave output | `:2e` | `9.00e-01` | shows 0 / VDD compactly |
| Digital control (rst_n, en, …) | `:d` | `1` | purely 0 or 1 |
| Bus bits (dout_N, code_N, …) | `:d` | `0` | integer, combined into `_code` column |
| Digital gate output (q, y, …) | `:2e` | `8.00e-01` | square wave, may show transitions |

Auto-derived `*_code` columns always use `:d` regardless of suffix.

```
// Canonical example
save vin:3f clk:2e rst_n:d vout:3f dout_3:d dout_2:d dout_1:d dout_0:d
```

---

After simulation each example produces two files under `output/<name>/`:

| File | Contents |
|------|----------|
| `tran.csv` | Transient waveform table. Columns: `time` (seconds) + one column per node. |
| `strobe.txt` | All `$strobe` output lines from the simulator, in time order. |

### CSV column naming

- Scalar port `vout` → column `vout`
- Bus port `dout[7:0]` → columns `dout_7`, `dout_6`, … `dout_0`
- Bus columns are automatically aggregated into a `dout_code` integer column
  (MSB = highest index) by the EVAS post-processor.

---

## 3. Testbench (`tb_<name>.scs`) Conventions

- One Spectre netlist per module, in `examples/<name>/tb_<name>.scs`.
- Supply sources named `vvdd` / `vvss`; input stimuli named `v<portname>`.
- DUT instance named `dut`.
- Bus node naming: array port `output [7:0] dout` → nodes listed MSB-first:
  `dout_7 dout_6 dout_5 dout_4 dout_3 dout_2 dout_1 dout_0`.
- Transient simulation line:
  ```
  tran tran stop=<time> strobeperiod=<period> method=gear
  ```

---

## 4. Analysis Script (`analyze_<name>.py`) Conventions

```python
from evas import evas_simulate

out_dir = evas_simulate("examples/<name>/tb_<name>.scs")

import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv(f"{out_dir}/tran.csv")
# ... stacked subplots, one per signal of interest ...
plt.savefig(f"{out_dir}/waveform.png", dpi=150)
```

---

## 5. Validation Script (`validate_<name>.py`) Conventions

One file per module with two top-level functions plus a `main()`:

```python
import re, sys
import pandas as pd

OUT_DIR = "output/<name>"

def validate_csv(out_dir: str) -> int:
    """Assert waveform properties from tran.csv. Returns failure count."""
    df = pd.read_csv(f"{out_dir}/tran.csv")
    failures = 0
    # Example assertion:
    # assert df["vout"].max() > 0.5, "output never goes high"
    return failures

def validate_txt(out_dir: str) -> int:
    """Assert $strobe output from strobe.txt. Returns failure count."""
    failures = 0
    with open(f"{out_dir}/strobe.txt") as f:
        lines = f.readlines()
    # Example: check INIT line present
    init_lines = [l for l in lines if "[module_name] INIT |" in l]
    if not init_lines:
        print("FAIL: no INIT line found")
        failures += 1
    # Example: parse event lines with regex
    pattern = re.compile(
        r"\[module_name\] t=(?P<t>[\d.]+) ns \| key=(?P<v>[\d.e+\-]+)"
    )
    for line in lines:
        m = pattern.search(line)
        if m:
            pass  # check m.group("v") etc.
    return failures

def main():
    f = validate_csv(OUT_DIR) + validate_txt(OUT_DIR)
    print(f"{'PASS' if f == 0 else 'FAIL'}: {f} failure(s)")
    sys.exit(0 if f == 0 else 1)

if __name__ == "__main__":
    main()
```

### Validation guidelines

- `validate_csv`: Check voltage ranges, code values, monotonicity, or timing
  extracted from the waveform columns.
- `validate_txt`: Use `re.compile` patterns to extract key fields from the
  `[module_name]: Time=… ns, …` lines; assert expected relationships.
- Both functions return an integer failure count so they compose cleanly.
- `main()` exits with code `0` on all-pass, `1` on any failure.
