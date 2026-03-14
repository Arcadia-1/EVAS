"""Validate cmp_ideal: ideal clocked comparator.

Testbench: VCM=0.45V (VDD/2), diff=1mV, polarity swaps at 5ns. Clock: 1GHz, VDD=0.9V.
Expected: out_p HIGH before swap, LOW after swap. No delay.
"""
from pathlib import Path

import numpy as np

OUT = Path(__file__).parent.parent.parent / 'output' / 'comparator' / 'cmp_ideal'

_VTH = 0.45


def validate_csv(out_dir: Path = OUT) -> int:
    data = np.genfromtxt(out_dir / 'tran.csv', delimiter=',', names=True, dtype=None, encoding='utf-8')
    failures = 0
    t_ns  = data['time'] * 1e9
    out_p = data['out_p']

    if out_p.max() - out_p.min() < _VTH:
        print("FAIL: out_p never toggles")
        failures += 1

    pre_p_hi = (out_p[t_ns < 4.0] > _VTH).mean()
    if pre_p_hi < 0.4:
        print(f"FAIL: before swap, out_p HIGH only {pre_p_hi*100:.0f}% (expected >40%)")
        failures += 1

    post_p_lo = (out_p[t_ns > 6.0] < _VTH).mean()
    if post_p_lo < 0.4:
        print(f"FAIL: after swap, out_p LOW only {post_p_lo*100:.0f}% (expected >40%)")
        failures += 1

    if failures == 0:
        print("[CSV] All assertions passed.")
    return failures


if __name__ == '__main__':
    raise SystemExit(0 if validate_csv() == 0 else 1)
