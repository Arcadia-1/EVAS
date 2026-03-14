"""Analyze d2b_4b: unified static code driver (trim_code=9)."""
import time
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
_DEFAULT_OUT = HERE.parent.parent.parent / 'output' / 'd2b_4b'


def analyze(out_dir: Path = _DEFAULT_OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    evas_simulate(str(HERE / 'tb_d2b_4b.scs'), output_dir=str(out_dir))
    wall_s = time.perf_counter() - t0

    data = np.genfromtxt(out_dir / 'tran.csv', delimiter=',', names=True, dtype=None, encoding='utf-8')
    t = data['time'] * 1e9

    bin_cols      = [f'bin_o_{i}'      for i in range(4)]
    bin_n_cols    = [f'bin_n_o_{i}'    for i in range(4)]
    onehot_cols   = [f'onehot_o_{i}'   for i in range(16)]
    onehot_n_cols = [f'onehot_n_o_{i}' for i in range(16)]
    therm_cols    = [f'therm_o_{i}'    for i in range(15)]
    therm_n_cols  = [f'therm_n_o_{i}'  for i in range(15)]

    fig, axes = plt.subplots(6, 1, figsize=(12, 14), sharex=True)
    fig.suptitle(f'd2b_4b (trim_code=9)  |  wall clock: {wall_s:.4f} s', fontsize=12)

    def plot_stacked(ax, cols, title):
        for i, col in enumerate(cols):
            if col in list(data.dtype.names):
                ax.plot(t, data[col] + i * 1.1, linewidth=1.0,
                        drawstyle='steps-post',
                        label=col if i in [0, len(cols) - 1] else '')
        ax.set_ylabel('bits (stacked)')
        ax.set_title(title)
        ax.legend(loc='upper right', fontsize=7)
        ax.grid(True, alpha=0.3)

    plot_stacked(axes[0], bin_cols,      'bin_o[3:0] — binary active-high')
    plot_stacked(axes[1], bin_n_cols,    'bin_n_o[3:0] — binary active-low')
    plot_stacked(axes[2], onehot_cols,   'onehot_o[15:0] — one-hot active-high')
    plot_stacked(axes[3], onehot_n_cols, 'onehot_n_o[15:0] — one-cold active-low')
    plot_stacked(axes[4], therm_cols,    'therm_o[14:0] — thermometer active-high')
    plot_stacked(axes[5], therm_n_cols,  'therm_n_o[14:0] — thermometer active-low')

    axes[0].set_xlim(t[0], t[-1])
    axes[-1].set_xlabel('Time (ns)')
    fig.tight_layout()
    fig.savefig(str(out_dir / 'analyze_d2b_4b.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Plot saved: {out_dir / 'analyze_d2b_4b.png'}")


if __name__ == "__main__":
    analyze()
