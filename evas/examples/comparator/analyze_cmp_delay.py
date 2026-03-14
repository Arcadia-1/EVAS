"""Analyze cmp_delay: comparator delay inversely proportional to |Vdiff|.

Four phases with decreasing differential: 10mV, 2mV, 1mV, 0.5mV.
Expected delays: ~100ps, ~500ps, ~1ns, ~2ns.
"""
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
_DEFAULT_BASE = HERE.parent.parent / 'output' / 'comparator'

_PHASES = [
    (0,  20,  10.0),
    (20, 40,   2.0),
    (40, 60,   1.0),
    (60, 80,   0.5),
]


def analyze(base_dir: Path = _DEFAULT_BASE) -> None:
    out_dir = base_dir / 'cmp_delay'
    out_dir.mkdir(parents=True, exist_ok=True)

    evas_simulate(str(HERE / 'tb_cmp_delay.scs'), output_dir=str(out_dir))

    data = np.genfromtxt(out_dir / 'tran.csv', delimiter=',', names=True, dtype=None, encoding='utf-8')
    t    = data['time'] * 1e9  # ns
    vdiff = (data['vinp'] - data['vinn']) * 1e3  # mV

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True,
                             gridspec_kw={'height_ratios': [1.5, 2, 3]})
    fig.suptitle('cmp_delay — Output Delay ∝ 1/|Vdiff|  (td_scale=1 ps·V, VCM=VDD/2)')

    # CLK
    axes[0].plot(t, data['clk'], linewidth=1.0, color='gray')
    axes[0].set_ylabel('clk (V)')
    vdd = data['clk'].max()
    axes[0].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[0].grid(True, alpha=0.3)

    # Differential
    axes[1].plot(t, vdiff, linewidth=1.0, color='tab:purple')
    axes[1].axhline(0, color='gray', linewidth=0.8, linestyle='--')
    axes[1].set_ylabel('VINP−VINN (mV)')
    axes[1].grid(True, alpha=0.3)
    for t0, t1, diff_mv in _PHASES:
        axes[1].annotate(f'{diff_mv:g} mV', xy=((t0 + t1) / 2, diff_mv),
                         ha='center', va='bottom', fontsize=8, color='tab:purple')

    # Output with per-phase delay annotation
    axes[2].plot(t, data['out_p'], linewidth=1.0, color='tab:blue', label='out_p')
    axes[2].set_ylabel('out_p (V)')
    axes[2].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[2].legend(loc='upper right')
    axes[2].grid(True, alpha=0.3)

    # Annotate expected delay per phase
    clk_rise_ns = 1.0  # first rising edge delay from testbench
    for t0, _, diff_mv in _PHASES:
        td_ns = 1e-12 / (diff_mv * 1e-3) * 1e9
        td_ns = min(td_ns, 8.0)
        tr = t0 + clk_rise_ns
        axes[2].annotate(f'td≈{td_ns:.1f}ns', xy=(tr + td_ns / 2, vdd * 0.5),
                         ha='center', va='center', fontsize=8,
                         bbox=dict(boxstyle='round,pad=0.2', fc='lightyellow', ec='gray', alpha=0.8))

    axes[-1].set_xlabel('Time (ns)')
    fig.tight_layout()
    fig.savefig(str(base_dir / 'analyze_cmp_delay.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Plot saved: {base_dir / 'analyze_cmp_delay.png'}")


if __name__ == "__main__":
    analyze()
