"""Validate dac_binary_8b: 8-bit binary DAC (vstep=0.05V).

Steps applied:
  t=200ns: code=16  -> vout = 16 * 0.05 = 0.80V
  t=400ns: code=32  -> vout = 32 * 0.05 = 1.60V
  t=600ns: code=64  -> vout = 64 * 0.05 = 3.20V
  t=800ns: code=128 -> vout = 128 * 0.05 = 6.40V
  t=1000ns: code=192 -> vout = 192 * 0.05 = 9.60V
  t=1200ns: code=255 -> vout = 255 * 0.05 = 12.75V
"""
from pathlib import Path
import pandas as pd
import numpy as np

OUT = Path(__file__).parent.parent.parent / 'output' / 'dac_binary_8b'

# (sample_time_ns, expected_code, expected_vout_V)
_CHECKPOINTS = [
    (250.0,  16,   0.80),
    (450.0,  32,   1.60),
    (650.0,  64,   3.20),
    (850.0,  128,  6.40),
    (1050.0, 192,  9.60),
    (1300.0, 255,  12.75),
]
_VSTEP = 0.05
_TOL_FRAC = 0.05  # 5% tolerance


def validate_csv(out_dir: Path = OUT) -> int:
    df = pd.read_csv(out_dir / 'tran.csv')
    failures = 0

    t_ns = df['time'].values * 1e9
    din_cols = [f'din_bin_{i}' for i in range(8)]

    # Decode input code
    din_code = np.zeros(len(df), dtype=int)
    for i, col in enumerate(din_cols):
        if col in df.columns:
            din_code += ((df[col].values > 0.45).astype(int) << i)

    for t_check, exp_code, exp_vout in _CHECKPOINTS:
        idx = int(np.argmin(np.abs(t_ns - t_check)))
        got_vout = float(df['vout'].iloc[idx])
        tol = max(exp_vout * _TOL_FRAC, 0.1)
        if abs(got_vout - exp_vout) > tol:
            print(f"FAIL: at t={t_check}ns code={exp_code}: vout={got_vout:.3f}V, expected {exp_vout:.3f}V (tol={tol:.3f}V)")
            failures += 1

    # After reset (t<5ns), vout should be 0
    early_vout = df['vout'].values[t_ns < 4.0]
    if len(early_vout) > 0 and np.any(np.abs(early_vout) > 0.05):
        print(f"FAIL: vout should be 0 during reset, got max={np.abs(early_vout).max():.3f}V")
        failures += 1

    if failures == 0:
        print("[CSV] All assertions passed.")
    return failures


def validate_txt(out_dir: Path = OUT) -> int:
    txt_path = out_dir / 'strobe.txt'
    if not txt_path.exists():
        return 0
    # dac_binary_8b does not emit $strobe lines
    return 0


if __name__ == '__main__':
    f1 = validate_csv()
    f2 = validate_txt()
    total = f1 + f2
    print(f"Validation: {total} failure(s) [{f1} CSV, {f2} TXT]")
    raise SystemExit(0 if total == 0 else 1)
