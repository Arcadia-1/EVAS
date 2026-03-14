"""Visualize digital_basics: truth-table bit-grids for AND, OR, NOT, DFF.

Runs existing testbenches once each, samples CSV at steady-state time points
for each input combination, assembles truth-table matrices, plots as bit-grids.
"""
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap
import pandas as pd

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
OUT  = HERE.parent.parent / 'output' / 'digital_basics'
OUT.mkdir(parents=True, exist_ok=True)

THRESH = 0.4   # logic-1 threshold

CMAP = ListedColormap(['#f0f4ff', '#2563eb'])   # white / blue

# ── Helpers ───────────────────────────────────────────────────────────────────

def bit_at(df: pd.DataFrame, t_ns: float, col: str) -> int:
    """Sample a signal at the row closest to t_ns (time column in seconds)."""
    t_s = t_ns * 1e-9
    idx = (df['time'] - t_s).abs().idxmin()
    return 1 if float(df.loc[idx, col]) > THRESH else 0


def draw_grid(ax, data, title, cmap, col_labels, row_labels):
    n_rows, n_cols = data.shape
    ax.imshow(data, aspect='auto', cmap=cmap, vmin=0, vmax=1,
              interpolation='nearest')
    ax.set_xticks(np.arange(-0.5, n_cols, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n_rows, 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=1.5)
    ax.tick_params(which='minor', length=0)
    for r in range(n_rows):
        for c in range(n_cols):
            v = data[r, c]
            ax.text(c, r, str(v), ha='center', va='center',
                    fontsize=9, color='white' if v else '#444444',
                    fontweight='bold')
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(col_labels, fontsize=9)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(row_labels, fontsize=9)
    ax.set_title(title, fontsize=10, pad=5)
    ax.xaxis.set_label_position('top')
    ax.xaxis.tick_top()


# ── 1. AND gate ───────────────────────────────────────────────────────────────
# Testbench: (A,B) cycles 00→01→10→11, 2ns each, sample midpoints 1,3,5,7 ns
evas_simulate(str(HERE / 'tb_and_gate.scs'), output_dir=str(OUT / 'and'),
              log_path=str(OUT / 'and' / 'sim.log'))
df_and = pd.read_csv(OUT / 'and' / 'tran.csv')

and_rows = [('0','0'), ('0','1'), ('1','0'), ('1','1')]
and_samples = [1.0, 3.0, 5.0, 7.0]   # ns midpoints
and_mat = np.array([
    [bit_at(df_and, t, 'a'), bit_at(df_and, t, 'b'), bit_at(df_and, t, 'y')]
    for t in and_samples
])

# ── 2. OR gate ────────────────────────────────────────────────────────────────
evas_simulate(str(HERE / 'tb_or_gate.scs'), output_dir=str(OUT / 'or'),
              log_path=str(OUT / 'or' / 'sim.log'))
df_or = pd.read_csv(OUT / 'or' / 'tran.csv')

or_mat = np.array([
    [bit_at(df_or, t, 'a'), bit_at(df_or, t, 'b'), bit_at(df_or, t, 'y')]
    for t in and_samples   # same timing structure
])

# ── 3. NOT gate ───────────────────────────────────────────────────────────────
# Testbench: A toggles every 2ns → sample at 1ns (A=0) and 3ns (A=1)
evas_simulate(str(HERE / 'tb_not_gate.scs'), output_dir=str(OUT / 'not'),
              log_path=str(OUT / 'not' / 'sim.log'))
df_not = pd.read_csv(OUT / 'not' / 'tran.csv')

not_mat = np.array([
    [bit_at(df_not, t, 'a'), bit_at(df_not, t, 'y')]
    for t in [1.0, 3.0]   # A=0 at 1ns, A=1 at 3ns
])

# ── 4. DFF with synchronous reset ────────────────────────────────────────────
# Rising CLK edges at: 0.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5 ns
# Sample 0.5ns after each edge (settled output)
evas_simulate(str(HERE / 'tb_dff_rst.scs'), output_dir=str(OUT / 'dff'),
              log_path=str(OUT / 'dff' / 'sim.log'))
df_dff = pd.read_csv(OUT / 'dff' / 'tran.csv')

clk_edges_ns = [0.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5, 16.5, 18.5]
dff_sample_ns = [t + 0.5 for t in clk_edges_ns]   # 0.5ns after each rising edge
dff_mat = np.array([
    [bit_at(df_dff, t, 'rst'), bit_at(df_dff, t, 'd'),
     bit_at(df_dff, t, 'q'),   1 - bit_at(df_dff, t, 'q')]   # qbar = ~q
    for t in dff_sample_ns
])
dff_row_labels = [f'CLK↑ @{t:.1f}ns' for t in clk_edges_ns]

# ── Plot — 2 rows × 3 cols ────────────────────────────────────────────────────
fig = plt.figure(figsize=(14, 9))
fig.suptitle('digital_basics — truth tables from simulation results', fontsize=13)

# AND  (row 0, col 0)
ax_and = fig.add_subplot(2, 3, 1)
draw_grid(ax_and, and_mat, 'AND gate\nand_gate(a, b, out)',
          CMAP, ['A', 'B', 'OUT'],
          ['A=0,B=0', 'A=0,B=1', 'A=1,B=0', 'A=1,B=1'])

# OR  (row 0, col 1)
ax_or = fig.add_subplot(2, 3, 2)
draw_grid(ax_or, or_mat, 'OR gate\nor_gate(a, b, out)',
          CMAP, ['A', 'B', 'OUT'],
          ['A=0,B=0', 'A=0,B=1', 'A=1,B=0', 'A=1,B=1'])

# NOT  (row 0, col 2)
ax_not = fig.add_subplot(2, 3, 3)
draw_grid(ax_not, not_mat, 'NOT gate\nnot_gate(a, out)',
          CMAP, ['A', 'OUT'],
          ['A=0', 'A=1'])

# DFF  (row 1, spans all 3 cols)
ax_dff = fig.add_subplot(2, 1, 2)
draw_grid(ax_dff, dff_mat, 'DFF with sync reset — dff_rst(clk, d, rst, q, qbar)',
          CMAP, ['RST', 'D', 'Q', 'Q̄'],
          dff_row_labels)

fig.tight_layout(rect=[0, 0.02, 1, 0.96])

leg = [
    mpatches.Patch(color='#2563eb', label='1 (high)'),
    mpatches.Patch(color='#f0f4ff', label='0 (low)'),
]
fig.legend(handles=leg, loc='lower center', ncol=2, fontsize=9,
           bbox_to_anchor=(0.5, 0.0))

out_png = OUT / 'visualize_digital_basics.png'
fig.savefig(str(out_png), dpi=150, bbox_inches='tight')
plt.close(fig)
print(f"Saved: {out_png}")
