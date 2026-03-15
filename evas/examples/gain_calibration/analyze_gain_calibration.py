"""Analyze gain_calibration simulation results.

Plots:
  1. VAMP_diff waveform (first 10 us) — verifies dither signal
  2. Gain estimation convergence: A_est vs sample count N (2^8 .. 2^16)
"""

import re
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

HERE = Path(__file__).parent

GAIN_NOM  = 8.0
GAIN_ERR  = 0.08   # must match tb parameter
ACTUAL_GAIN = GAIN_NOM * (1.0 + GAIN_ERR)

_DEFAULT_OUT = HERE.parent.parent.parent / 'output' / 'gain_calibration'


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_csv(out: Path) -> pd.DataFrame:
    df = pd.read_csv(out / 'tran.csv')
    df['vamp_diff'] = df['vamp_p'] - df['vamp_n']
    df['time_us']   = df['time'] * 1e6
    return df


def _parse_strobe(out: Path) -> pd.DataFrame:
    """Return DataFrame with one row per gain_estimator milestone."""
    path = out / 'strobe.txt'
    if not path.exists():
        return pd.DataFrame()

    pattern = re.compile(r'\[gain_est\] N=(\d+) \| A_est=([0-9.]+)')
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        m = pattern.search(line)
        if m:
            rows.append({'N': int(m.group(1)), 'A_est': float(m.group(2))})
    return pd.DataFrame(rows)


# ── plots ─────────────────────────────────────────────────────────────────────

def _plot_waveform(df: pd.DataFrame, out: Path, wall_s: float):
    """Plot 1: VAMP_diff over first 10 us."""
    sub = df[df['time_us'] <= 10.0]

    fig, ax = plt.subplots(figsize=(8, 3))
    ax.plot(sub['time_us'], sub['vamp_diff'] * 1e3, color='#ff9500', lw=0.8)
    ax.set_xlabel('Time  [us]')
    ax.set_ylabel('VAMP_diff  [mV]')
    ax.set_xlim(sub['time_us'].iloc[0], sub['time_us'].iloc[-1])
    ax.grid(True, alpha=0.35)
    ax.set_title(
        f'VAMP_diff — dither through gain amp (first 10 us)\n'
        f'ACTUAL_GAIN = {ACTUAL_GAIN:.4f}  (GAIN_ERR = {GAIN_ERR*100:+.0f}%)   '
        f'wall clock: {wall_s:.4f} s'
    )
    fig.tight_layout()
    p = out / 'waveform.png'
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f'Saved: {p}')


def _plot_convergence(strobe: pd.DataFrame, out: Path, wall_s: float):
    """Plot 2: gain estimate vs sample count."""
    if strobe.empty:
        print('No strobe data — skipping convergence plot.')
        return

    N    = strobe['N'].values
    Aest = strobe['A_est'].values
    err_pct = (Aest - ACTUAL_GAIN) / ACTUAL_GAIN * 100.0

    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True)

    # ── Panel 1: A_est vs N ──
    axes[0].semilogx(N, Aest, 'o-', ms=6, color='#0071e3', lw=1.4,
                     label='A_est (cross-correlation)')
    axes[0].axhline(ACTUAL_GAIN, color='r', ls='--', lw=1.2,
                    label=f'actual = {ACTUAL_GAIN:.4f}')
    axes[0].set_ylabel('Gain estimate')
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.35, which='both')

    # ── Panel 2: error % vs N ──
    axes[1].semilogx(N, err_pct, 's-', ms=5, color='#ff9500', lw=1.2)
    axes[1].axhline(0, color='r', ls='--', lw=1.0)
    axes[1].set_ylabel('Error  [%]')
    axes[1].set_xlabel('Sample count  N')
    axes[1].grid(True, alpha=0.35, which='both')

    # x-axis ticks at powers of 2
    axes[1].set_xticks(N)
    axes[1].set_xticklabels([f'$2^{{{int(np.log2(n))}}}$' for n in N], fontsize=8)

    fig.suptitle(
        f'Gain estimation convergence  (GAIN_ERR = {GAIN_ERR*100:+.0f}%)\n'
        f'ACTUAL_GAIN = {ACTUAL_GAIN:.4f}   wall clock: {wall_s:.4f} s',
        fontsize=10,
    )
    fig.tight_layout()
    p = out / 'gain_convergence.png'
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f'Saved: {p}')


# ── main entry ────────────────────────────────────────────────────────────────

def analyze(output_dir=_DEFAULT_OUT):
    from evas.netlist.runner import evas_simulate

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    scs = HERE / 'tb_gain_calibration.scs'
    t0  = time.perf_counter()
    evas_simulate(str(scs), output_dir=str(out))
    wall_s = time.perf_counter() - t0

    df     = _load_csv(out)
    strobe = _parse_strobe(out)

    _plot_waveform(df, out, wall_s)
    _plot_convergence(strobe, out, wall_s)


if __name__ == '__main__':
    analyze()
