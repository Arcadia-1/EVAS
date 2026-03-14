"""Validate dac_onehot_inv_16b: 16-bit one-cold DAC (vstep=1.0V).

Checkpoints (one-cold: zero at bit i -> vout = i * 1.0):
  t=100ns:  zero at bit0  -> vout = 0.0V
  t=400ns:  zero at bit5  -> vout = 5.0V
  t=700ns:  zero at bit10 -> vout = 10.0V
  t=1000ns: zero at bit15 -> vout = 15.0V
"""
from pathlib import Path
import pandas as pd
import numpy as np

OUT = Path(__file__).parent.parent.parent / 'output' / 'dac_onehot_inv_16b'

_CHECKPOINTS = [
    (100.0,  0,   0.0),
    (400.0,  5,   5.0),
    (700.0,  10,  10.0),
    (1000.0, 15,  15.0),
]
_VSTEP = 1.0
_TOL = 0.1  # 0.1V tolerance


def validate_csv(out_dir: Path = OUT) -> int:
    df = pd.read_csv(out_dir / 'tran.csv')
    failures = 0

    t_ns = df['time'].values * 1e9

    for t_check, zero_bit, exp_vout in _CHECKPOINTS:
        idx = int(np.argmin(np.abs(t_ns - t_check)))
        got_vout = float(df['vout'].iloc[idx])
        if abs(got_vout - exp_vout) > _TOL:
            print(f"FAIL: at t={t_check}ns (zero_bit={zero_bit}): vout={got_vout:.3f}V, expected {exp_vout:.3f}V")
            failures += 1

    # vout during reset should be ~0
    early_vout = df['vout'].values[t_ns < 4.0]
    if len(early_vout) > 0 and np.any(np.abs(early_vout) > 0.05):
        print(f"FAIL: vout should be 0 during reset")
        failures += 1

    if failures == 0:
        print("[CSV] All assertions passed.")
    return failures


def validate_txt(out_dir: Path = OUT) -> int:
    txt_path = out_dir / 'strobe.txt'
    if not txt_path.exists():
        return 0
    # dac_onehot_inv_16b does not emit $strobe lines
    return 0


if __name__ == '__main__':
    f1 = validate_csv()
    f2 = validate_txt()
    total = f1 + f2
    print(f"Validation: {total} failure(s) [{f1} CSV, {f2} TXT]")
    raise SystemExit(0 if total == 0 else 1)
