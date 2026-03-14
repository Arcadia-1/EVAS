"""Visualize dwa_ptr_gen: for each code (1..15), run 16 consecutive DWA steps
(no reset between steps) and show cell_en_o at each step as a row in a heatmap.

This reveals the rotating selection window — the core DWA behavior.
One 4×4 grid: each subplot = one fixed code, rows = DWA step, cols = cell index.
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

HERE   = Path(__file__).parent
OUT    = HERE.parent.parent / 'output' / 'dwa_ptr_gen'
OUT.mkdir(parents=True, exist_ok=True)

VA_PATH   = (HERE / 'dwa_ptr_gen.va').resolve()
N_STEPS   = 16     # DWA cycles per simulation
N_CELLS   = 16
CLK_PERIOD_NS = 50
CLK_DELAY_NS  = 10
VDD  = 0.9
THRESH = VDD * 0.5

# ── Testbench template: N_STEPS clock cycles, static code, ptr_init=0 ─────────
TB_TEMPLATE = """\
simulator lang=spectre
global 0

ahdl_include "{va_path}"

// Clock: {n_steps} cycles, period={period}ns, first rising edge at {delay}ns
Vclk  (clk_i  0) vsource type=pulse val0=0 val1=0.9 \\
                  period={period}n delay={delay}n rise=1n fall=1n width={half}n

// Reset: deassert at 6ns (before first clock edge)
Vrst_n (rst_ni 0) vsource type=pwl wave=[0 0 5n 0 6n 0.9 {stop}n 0.9]

// Static code inputs (DC) for code={code}
Vcode0 (code_msb_i_0 0) vsource type=dc dc={bit0}
Vcode1 (code_msb_i_1 0) vsource type=dc dc={bit1}
Vcode2 (code_msb_i_2 0) vsource type=dc dc={bit2}
Vcode3 (code_msb_i_3 0) vsource type=dc dc={bit3}

XDUT (clk_i rst_ni \\
      code_msb_i_3 code_msb_i_2 code_msb_i_1 code_msb_i_0 \\
      cell_en_15 cell_en_14 cell_en_13 cell_en_12 cell_en_11 cell_en_10 cell_en_9 cell_en_8 \\
      cell_en_7  cell_en_6  cell_en_5  cell_en_4  cell_en_3  cell_en_2  cell_en_1  cell_en_0 \\
      ptr_15 ptr_14 ptr_13 ptr_12 ptr_11 ptr_10 ptr_9 ptr_8 \\
      ptr_7  ptr_6  ptr_5  ptr_4  ptr_3  ptr_2  ptr_1  ptr_0) \\
      dwa_ptr_gen vdd=0.9 vth=0.45 ptr_init=0

tran tran stop={stop}n maxstep=0.5n
save cell_en_15:d cell_en_14:d cell_en_13:d cell_en_12:d cell_en_11:d cell_en_10:d cell_en_9:d cell_en_8:d \\
     cell_en_7:d  cell_en_6:d  cell_en_5:d  cell_en_4:d  cell_en_3:d  cell_en_2:d  cell_en_1:d  cell_en_0:d \\
     ptr_15:d ptr_14:d ptr_13:d ptr_12:d ptr_11:d ptr_10:d ptr_9:d ptr_8:d \\
     ptr_7:d  ptr_6:d  ptr_5:d  ptr_4:d  ptr_3:d  ptr_2:d  ptr_1:d  ptr_0:d
"""

# Sample time: 5ns after each rising edge  (edge k at delay + k*period ns)
sample_times_ns = [CLK_DELAY_NS + k * CLK_PERIOD_NS + 5
                   for k in range(N_STEPS)]
STOP_NS = CLK_DELAY_NS + N_STEPS * CLK_PERIOD_NS + 10

CODES = list(range(16))

# ── Run simulations ────────────────────────────────────────────────────────────
# data[code] = (N_STEPS × N_CELLS) matrix of cell_en bits
cell_data = {}
ptr_data  = {}

for code in CODES:
    sim_out = OUT / f'dwa_code_{code}'
    sim_out.mkdir(parents=True, exist_ok=True)

    tb = TB_TEMPLATE.format(
        va_path   = VA_PATH.as_posix(),
        code      = code,
        bit0      = VDD if (code >> 0) & 1 else 0,
        bit1      = VDD if (code >> 1) & 1 else 0,
        bit2      = VDD if (code >> 2) & 1 else 0,
        bit3      = VDD if (code >> 3) & 1 else 0,
        n_steps   = N_STEPS,
        period    = CLK_PERIOD_NS,
        delay     = CLK_DELAY_NS,
        half      = CLK_PERIOD_NS // 2,
        stop      = STOP_NS,
    )
    tb_path = sim_out / 'tb_dwa_ptr_gen.scs'
    tb_path.write_text(tb, encoding='utf-8')

    print(f"[code={code:2d}] simulating {N_STEPS} steps ...", end=' ', flush=True)
    ok = evas_simulate(str(tb_path), output_dir=str(sim_out),
                       log_path=str(sim_out / 'sim.log'))
    if not ok:
        print("FAILED"); continue

    df   = pd.read_csv(sim_out / 'tran.csv')
    t_s  = df['time'].values

    def sample(col, t_ns):
        t_s_target = t_ns * 1e-9
        idx = np.argmin(np.abs(t_s - t_s_target))
        return 1 if float(df.iloc[idx][col]) > THRESH else 0

    cell_mat = np.array([
        [sample(f'cell_en_{i}', t) for i in range(N_CELLS)]
        for t in sample_times_ns
    ])   # (N_STEPS, N_CELLS)

    ptr_mat = np.array([
        [sample(f'ptr_{i}', t) for i in range(N_CELLS)]
        for t in sample_times_ns
    ])

    cell_data[code] = cell_mat
    ptr_data[code]  = ptr_mat

    ones_per_step = cell_mat.sum(axis=1)
    print(f"done  cell count/step: {list(ones_per_step)}")

# ── Color maps ────────────────────────────────────────────────────────────────
CMAP_CELL = ListedColormap(['#f0f9ff', '#0369a1'])   # cell_en: white / blue
CMAP_PTR  = ListedColormap(['#fdf4ff', '#7e22ce'])   # ptr:     white / purple

# ── Plot — 4×4 grid, one subplot per code ─────────────────────────────────────
fig, axes = plt.subplots(4, 4, figsize=(20, 16),
                         gridspec_kw={'hspace': 0.5, 'wspace': 0.1})
fig.suptitle(
    f'dwa_ptr_gen — {N_STEPS} consecutive DWA steps per code (ptr_init=0)\n'
    'cell_en_o[15:0]: blue=selected  ptr_o[15:0]: purple border',
    fontsize=12
)

col_labels = [str(i) for i in range(N_CELLS)]
row_labels  = [f's{k+1}' for k in range(N_STEPS)]

for code in CODES:
    ax = axes[code // 4][code % 4]

    if code not in cell_data:
        ax.set_visible(False)
        continue

    cmat = cell_data[code]
    pmat = ptr_data[code]

    # Base: cell_en coloring
    ax.imshow(cmat, aspect='auto', cmap=CMAP_CELL, vmin=0, vmax=1,
              interpolation='nearest')

    # Grid lines
    ax.set_xticks(np.arange(-0.5, N_CELLS, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, N_STEPS, 1), minor=True)
    ax.grid(which='minor', color='white', linewidth=0.8)
    ax.tick_params(which='minor', length=0)

    # Cell text + purple highlight for ptr position
    for r in range(N_STEPS):
        for c in range(N_CELLS):
            v   = cmat[r, c]
            is_ptr = pmat[r, c]
            txt_color = 'white' if v else '#555555'
            ax.text(c, r, str(v), ha='center', va='center',
                    fontsize=5.5, color=txt_color, fontweight='bold')
            if is_ptr:
                rect = plt.Rectangle((c - 0.48, r - 0.48), 0.96, 0.96,
                                     linewidth=1.8, edgecolor='#7e22ce',
                                     facecolor='none')
                ax.add_patch(rect)

    ax.set_xticks(range(N_CELLS))
    ax.set_xticklabels(col_labels, fontsize=5)
    ax.set_yticks(range(N_STEPS))
    ax.set_yticklabels(row_labels, fontsize=5)
    ax.xaxis.set_label_position('top')
    ax.xaxis.tick_top()

    # Title: code + usage count summary
    total_uses = cell_data[code].sum(axis=0)   # how many times each cell was used
    eq = np.all(total_uses == total_uses[0])
    title_suffix = '  ✓ balanced' if eq else f'  uses: {list(total_uses)}'
    ax.set_title(f'code={code}  ({code:#05b}){title_suffix}',
                 fontsize=7, pad=3)

leg = [
    mpatches.Patch(color='#0369a1', label='cell_en=1  (cell active)'),
    mpatches.Patch(color='#f0f9ff', label='cell_en=0'),
    mpatches.Patch(facecolor='none', edgecolor='#7e22ce', linewidth=1.8,
                   label='ptr position'),
]
fig.legend(handles=leg, loc='lower center', ncol=3, fontsize=9,
           bbox_to_anchor=(0.5, 0.005))

out_png = OUT / 'visualize_dwa_ptr_gen.png'
fig.savefig(str(out_png), dpi=150, bbox_inches='tight')
plt.close(fig)
print(f"\nSaved: {out_png}")
