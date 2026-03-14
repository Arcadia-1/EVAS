"""Analyze lfsr: Linear Feedback Shift Register output."""
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
_DEFAULT_OUT = HERE.parent.parent / 'output' / 'lfsr'


def analyze(out_dir: Path = _DEFAULT_OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    evas_simulate(str(HERE / 'tb_lfsr.scs'), output_dir=str(out_dir))

    data = np.genfromtxt(out_dir / 'tran.csv', delimiter=',', names=True, dtype=None, encoding='utf-8')
    t  = data['time'] * 1e9  # → ns

    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True,
                             gridspec_kw={'height_ratios': [1.5, 1.5, 3]})
    fig.suptitle('LFSR')

    axes[0].plot(t, data['rstb'], linewidth=1.0)
    axes[0].set_ylabel('rstb (V)')
    vdd = max(data[c].max() for c in ['rstb', 'clk', 'dpn'])
    axes[0].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(t, data['clk'], linewidth=1.0)
    axes[1].set_ylabel('clk (V)')
    axes[1].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(t, data['dpn'], linewidth=1.0, color='tab:green')
    axes[2].set_ylabel('dpn (V)')
    axes[2].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[2].grid(True, alpha=0.3)

    axes[-1].set_xlabel('Time (ns)')
    fig.tight_layout()
    fig.savefig(str(out_dir / 'analyze_lfsr.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Plot saved: {out_dir / 'analyze_lfsr.png'}")


if __name__ == "__main__":
    analyze()
