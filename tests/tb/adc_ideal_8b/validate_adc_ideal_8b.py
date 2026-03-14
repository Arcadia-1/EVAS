"""Validate adc_ideal_8b behavior from CSV output."""
from pathlib import Path
import pandas as pd
import numpy as np

OUT = Path(__file__).parent.parent.parent / 'output' / 'adc_ideal_8b'


def validate_csv(out_dir: Path = OUT) -> int:
    df = pd.read_csv(out_dir / 'tran.csv')
    failures = 0

    # rst_n should reach 0.8V
    if df['rst_n'].max() < 0.7:
        print("FAIL: rst_n never went high")
        failures += 1

    # After reset deassert, dout_code should be non-trivially active
    # With vstep=0.1, vin=1.0V -> code=10, vin=10.0V -> code=100
    # Sample at ~100ns (vin~2.0V -> code~20)
    t_ns = df['time'].values * 1e9
    idx_100 = np.argmin(np.abs(t_ns - 100.0))
    code_at_100 = df['dout_code'].iloc[idx_100]
    expected_code_100 = 20  # vin=2.0V / 0.1 = 20
    if abs(code_at_100 - expected_code_100) > 2:
        print(f"FAIL: at t=100ns expected dout_code~{expected_code_100}, got {code_at_100}")
        failures += 1

    # Sample at ~500ns (vin~10.0V -> code~100)
    idx_500 = np.argmin(np.abs(t_ns - 500.0))
    code_at_500 = df['dout_code'].iloc[idx_500]
    expected_code_500 = 100
    if abs(code_at_500 - expected_code_500) > 2:
        print(f"FAIL: at t=500ns expected dout_code~{expected_code_500}, got {code_at_500}")
        failures += 1

    # dout_code should be monotonically non-decreasing (after reset)
    active = df[df['time'] > 10e-9]['dout_code'].values
    diffs = np.diff(active)
    if np.any(diffs < -1):
        print("FAIL: dout_code decreased unexpectedly (non-monotonic)")
        failures += 1

    # Max code should be ~255 (vin max = 25.5V, vstep=0.1 -> 255)
    if df['dout_code'].max() < 250:
        print(f"FAIL: max dout_code={df['dout_code'].max()}, expected ~255")
        failures += 1

    if failures == 0:
        print("[CSV] All assertions passed.")
    return failures


def validate_txt(out_dir: Path = OUT) -> int:
    txt_path = out_dir / 'strobe.txt'
    if not txt_path.exists():
        return 0
    # adc_ideal_8b does not emit $strobe lines
    return 0


if __name__ == '__main__':
    f1 = validate_csv()
    f2 = validate_txt()
    total = f1 + f2
    print(f"Validation: {total} failure(s) [{f1} CSV, {f2} TXT]")
    raise SystemExit(0 if total == 0 else 1)
