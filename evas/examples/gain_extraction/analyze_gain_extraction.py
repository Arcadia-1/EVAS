"""Analyze gain_extraction simulation results.

Plots:
  1. waveform.png  — VIN_diff & VAMP_diff (first 10 us, from EVAS)
  2. gain_convergence.png — A_est vs N for 3 noise seeds (NumPy, 2^8..2^18)
"""

import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent

# ── Parameters (must match tb_gain_extraction.scs) ───────────────────────────
GAIN_NOM    = 8.0
GAIN_ERR    = 0.08
ACTUAL_GAIN = GAIN_NOM * (1.0 + GAIN_ERR)   # 8.64
DITHER_AMP  = 0.014063
VIN_AMPL    = 0.15
VIN_NOISE   = 0.01
FIN         = 300e3
FS          = 50e6

# ── Convergence analysis settings ────────────────────────────────────────────
SEEDS      = [42, 123, 7]
N_CONV     = 2 ** 18
MILESTONES = [2 ** k for k in range(8, 19)]   # 2^8 .. 2^18

_DEFAULT_OUT = HERE.parent.parent.parent / 'output' / 'gain_extraction'

COLORS = ['#0071e3', '#ff9500', '#34c759']


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_csv(out: Path) -> pd.DataFrame:
    df = pd.read_csv(out / 'tran.csv')
    df['vin_diff']  = df['vinp'] - df['vinn']
    df['vamp_diff'] = df['vamp_p'] - df['vamp_n']
    df['time_us']   = df['time'] * 1e6
    return df


# ── plots ─────────────────────────────────────────────────────────────────────

def _plot_waveform(df: pd.DataFrame, out: Path, wall_evas_s: float):
    sub = df[df['time_us'] <= 10.0]

    fig, axes = plt.subplots(2, 1, figsize=(8, 5), sharex=True)

    axes[0].plot(sub['time_us'], sub['vin_diff'] * 1e3, color='#34c759', lw=0.8)
    axes[0].set_ylabel('VIN_diff  [mV]')
    axes[0].set_xlim(sub['time_us'].iloc[0], sub['time_us'].iloc[-1])
    axes[0].grid(True, alpha=0.35)
    axes[0].set_title(
        f'Input & output waveforms (first 10 us)\n'
        f'ACTUAL_GAIN = {ACTUAL_GAIN:.4f}   noise σ = {VIN_NOISE*1e3:.1f} mV   '
        f'EVAS wall clock: {wall_evas_s:.2f} s'
    )

    axes[1].plot(sub['time_us'], sub['vamp_diff'] * 1e3, color='#ff9500', lw=0.8)
    axes[1].set_ylabel('VAMP_diff  [mV]')
    axes[1].set_xlabel('Time  [us]')
    axes[1].grid(True, alpha=0.35)

    fig.tight_layout()
    p = out / 'waveform.png'
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f'Saved: {p}')


def _plot_convergence(out: Path, _unused: float):
    t0  = time.perf_counter()
    Ns = np.array(MILESTONES)
    k_arr = np.arange(N_CONV)
    t_arr = k_arr / FS

    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)

    for idx, seed in enumerate(SEEDS):
        rng  = np.random.default_rng(seed)
        dpn  = rng.choice([-1.0, 1.0], size=N_CONV)
        vin  = VIN_AMPL * np.sin(2 * np.pi * FIN * t_arr) \
               + VIN_NOISE * rng.standard_normal(N_CONV)
        vamp = ACTUAL_GAIN * (vin + DITHER_AMP * dpn)

        cumcorr = np.cumsum(vamp * dpn)
        Aest    = cumcorr[Ns - 1] / (Ns * DITHER_AMP)
        err_pct = (Aest - ACTUAL_GAIN) / ACTUAL_GAIN * 100.0

        label = f'seed = {seed}'
        axes[0].semilogx(Ns, Aest, 'o-', ms=4, color=COLORS[idx], lw=1.2, label=label)
        axes[1].semilogx(Ns, err_pct, 's-', ms=4, color=COLORS[idx], lw=1.2)

    axes[0].axhline(ACTUAL_GAIN, color='r', ls='--', lw=1.0,
                    label=f'actual = {ACTUAL_GAIN:.4f}')
    axes[0].set_ylabel('Gain estimate')
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.35, which='both')

    axes[1].axhline(0, color='r', ls='--', lw=1.0)
    axes[1].set_ylabel('Error  [%]')
    axes[1].set_xlabel('Sample count  N')
    axes[1].grid(True, alpha=0.35, which='both')

    axes[1].set_xticks(Ns)
    axes[1].set_xticklabels([f'$2^{{{int(np.log2(n))}}}$' for n in Ns], fontsize=8)

    fig.suptitle(
        f'Gain estimation convergence  (GAIN_ERR = {GAIN_ERR*100:+.0f}%,  '
        f'noise σ = {VIN_NOISE*1e3:.1f} mV,  3 seeds)\n'
        f'ACTUAL_GAIN = {ACTUAL_GAIN:.4f}   NumPy wall clock: {time.perf_counter()-t0:.3f} s',
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

    # 1. Short EVAS simulation for waveform verification
    scs = HERE / 'tb_gain_extraction.scs'
    t0 = time.perf_counter()
    evas_simulate(str(scs), output_dir=str(out))
    wall_evas_s = time.perf_counter() - t0

    df = _load_csv(out)
    _plot_waveform(df, out, wall_evas_s)

    # 2. NumPy convergence analysis (3 seeds x 2^18 samples)
    t0 = time.perf_counter()
    _plot_convergence(out, 0.0)   # timing measured inside
    wall_numpy_s = time.perf_counter() - t0
    print(f'NumPy convergence: {wall_numpy_s:.3f} s')


if __name__ == '__main__':
    analyze()
