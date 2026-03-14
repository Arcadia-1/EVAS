"""Analyze dwa_ptr_gen: DWA pointer rotation generator."""
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
_DEFAULT_OUT = HERE.parent.parent / 'output' / 'dwa_ptr_gen'


def analyze(out_dir: Path = _DEFAULT_OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Simulate
    evas_simulate(str(HERE / 'tb_dwa_ptr_gen.scs'), output_dir=str(out_dir))

    # 2. Load results
    data = np.genfromtxt(out_dir / 'tran.csv', delimiter=',', names=True, dtype=None, encoding='utf-8')
    t  = data['time'] * 1e9  # -> ns

    # Decode ptr_o as one-hot position (which bit is high)
    ptr_cols  = [f'ptr_{i}' for i in range(16)]
    cell_cols = [f'cell_en_{i}' for i in range(16)]

    ptr_pos = np.full(len(data), -1, dtype=int)
    for i, col in enumerate(ptr_cols):
        if col in list(data.dtype.names):
            mask = data[col] > 0.45
            ptr_pos[mask] = i

    cell_count = np.zeros(len(data), dtype=int)
    for col in cell_cols:
        if col in list(data.dtype.names):
            cell_count += (data[col] > 0.45).astype(int)

    # 3. Plot
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

    axes[0].plot(t, data['clk_i'], linewidth=1.0, drawstyle='steps-post', label='clk_i')
    axes[0].plot(t, data['rst_ni'], linewidth=1.0, drawstyle='steps-post', label='rst_ni', alpha=0.7)
    vdd = max(data[c].max() for c in ['clk_i', 'rst_ni'])
    axes[0].set_ylabel('clk / rst')
    axes[0].set_ylim(-vdd * 0.1, vdd * 1.2)
    axes[0].legend(fontsize=8)
    axes[0].set_title('dwa_ptr_gen (DWA pointer rotation)')
    axes[0].grid(True, alpha=0.3)

    # Input code
    code_cols = [f'code_msb_i_{i}' for i in range(4)]
    din_code = np.zeros(len(data), dtype=int)
    for i, col in enumerate(code_cols):
        if col in list(data.dtype.names):
            din_code += ((data[col] > 0.45).astype(int) << i)
    axes[1].plot(t, din_code, linewidth=1.0, drawstyle='steps-post', color='steelblue')
    axes[1].set_ylabel('code_msb_i')
    axes[1].set_ylim(-1, 16)
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(t, ptr_pos, linewidth=1.0, drawstyle='steps-post', color='purple')
    axes[2].set_ylabel('ptr position (one-hot idx)')
    axes[2].set_ylim(-2, 17)
    axes[2].grid(True, alpha=0.3)

    axes[3].plot(t, cell_count, linewidth=1.0, drawstyle='steps-post', color='darkorange')
    axes[3].set_ylabel('cell_en count')
    axes[3].set_ylim(-1, 18)
    axes[3].grid(True, alpha=0.3)

    axes[-1].set_xlabel('Time (ns)')
    fig.tight_layout()
    fig.savefig(str(out_dir / 'analyze_dwa_ptr_gen.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Plot saved: {out_dir / 'analyze_dwa_ptr_gen.png'}")


if __name__ == "__main__":
    analyze()
