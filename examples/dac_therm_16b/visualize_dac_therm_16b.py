"""Visualize dac_therm_16b: simulate all 17 thermometer states (0..16 ones),
read CSV results, plot input bit-grid + annotated vout column.

For ones_count=k: bits 0..k-1 driven high, bits k..15 driven low (LSB-first therm).
"""
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap, Normalize
import matplotlib.cm as cm
import pandas as pd

from evas.netlist.runner import evas_simulate

HERE = Path(__file__).parent
OUT  = HERE.parent.parent / 'output' / 'dac_therm_16b'
OUT.mkdir(parents=True, exist_ok=True)

VA_PATH = (HERE / 'dac_therm_16b.va').resolve()
VDD_IN  = 0.9    # logic-high drive voltage
THRESH  = 0.45   # midpoint threshold
VSTEP   = 1.0    # DAC step voltage

TB_TEMPLATE = """\
simulator lang=spectre
global 0

ahdl_include "{va_path}"

// Reset always high (no reset)
Vrst_n (rst_n 0) vsource type=dc dc=0.9

// Thermometer input: {ones_count} ones (bits 0..{ones_count_m1} high)
{bit_sources}

// DUT: din_therm[15:0] rst_n vout  (MSB-first bus order)
XDUT (d15 d14 d13 d12 d11 d10 d9 d8 d7 d6 d5 d4 d3 d2 d1 d0 \\
      rst_n vout) dac_therm_16b vstep={vstep} vth=0.4

tran tran stop=50n maxstep=1n
save d15:d d14:d d13:d d12:d d11:d d10:d d9:d d8:d d7:d d6:d d5:d d4:d d3:d d2:d d1:d d0:d vout:3f
"""

ONES_COUNTS = list(range(17))   # 0..16

# ── Simulate ──────────────────────────────────────────────────────────────────
rows = {}
for k in ONES_COUNTS:
    sim_out = OUT / f'ones_{k}'
    sim_out.mkdir(parents=True, exist_ok=True)

    bit_srcs = '\n'.join(
        f'Vd{i}  (d{i}  0) vsource type=dc dc={VDD_IN if i < k else 0}'
        for i in range(16)
    )
    tb = TB_TEMPLATE.format(
        va_path=VA_PATH.as_posix(),
        ones_count=k,
        ones_count_m1=max(k - 1, 0),
        bit_sources=bit_srcs,
        vstep=VSTEP,
    )
    tb_path = sim_out / 'tb_dac_therm_16b.scs'
    tb_path.write_text(tb, encoding='utf-8')

    print(f"[ones={k:2d}] simulating ...", end=' ', flush=True)
    ok = evas_simulate(str(tb_path), output_dir=str(sim_out),
                       log_path=str(sim_out / 'sim.log'))
    if not ok:
        print("FAILED"); continue

    df = pd.read_csv(sim_out / 'tran.csv')
    rows[k] = df.iloc[-1]
    vout = float(rows[k].get('vout', float('nan')))
    print(f"done  vout={vout:.3f}V")

# ── Build matrices ────────────────────────────────────────────────────────────
def bit(row, col):
    return 1 if float(row.get(col, 0)) > THRESH else 0

# Display order: bit15 (MSB) left → bit0 (LSB) right
bit_mat  = np.array([[bit(rows[k], f'd{i}') for i in range(15, -1, -1)]
                     for k in ONES_COUNTS])   # (17, 16)
vout_vec = np.array([float(rows[k].get('vout', float('nan'))) for k in ONES_COUNTS])

# ── Color maps ────────────────────────────────────────────────────────────────
CMAP_IN   = ListedColormap(['#f0f9ff', '#0369a1'])
CMAP_VOUT = cm.get_cmap('YlOrRd')
NORM_VOUT = Normalize(vmin=0, vmax=16 * VSTEP)

# ── Plot ──────────────────────────────────────────────────────────────────────
n_rows, n_cols = bit_mat.shape   # 17 × 16

fig = plt.figure(figsize=(14, 8))
# Wider axes for bit grid, narrow for vout bar
ax_bits = fig.add_axes([0.09, 0.12, 0.72, 0.74])
ax_vout = fig.add_axes([0.83, 0.12, 0.08, 0.74])
ax_cbar = fig.add_axes([0.93, 0.12, 0.02, 0.74])
fig.suptitle('dac_therm_16b — all 17 thermometer states, from simulation results',
             fontsize=12)

# Bit grid
ax_bits.imshow(bit_mat, aspect='auto', cmap=CMAP_IN, vmin=0, vmax=1,
               interpolation='nearest')
ax_bits.set_xticks(np.arange(-0.5, n_cols, 1), minor=True)
ax_bits.set_yticks(np.arange(-0.5, n_rows, 1), minor=True)
ax_bits.grid(which='minor', color='white', linewidth=1.0)
ax_bits.tick_params(which='minor', length=0)
for r in range(n_rows):
    for c in range(n_cols):
        v = bit_mat[r, c]
        ax_bits.text(c, r, str(v), ha='center', va='center',
                     fontsize=6.5, color='white' if v else '#555555',
                     fontweight='bold')
ax_bits.set_xticks(range(n_cols))
ax_bits.set_xticklabels([f'd{i}' for i in range(15, -1, -1)], fontsize=7)
ax_bits.xaxis.set_label_position('top'); ax_bits.xaxis.tick_top()
ax_bits.set_yticks(range(n_rows))
ax_bits.set_yticklabels([f'{k:2d} ones' for k in ONES_COUNTS], fontsize=8)
ax_bits.set_title('din_therm[15:0]  — thermometer input', fontsize=9, pad=4)
ax_bits.set_ylabel('input ones count', fontsize=9)

# vout bar
vout_img = vout_vec.reshape(-1, 1)
ax_vout.imshow(vout_img, aspect='auto', cmap=CMAP_VOUT, norm=NORM_VOUT,
               interpolation='nearest')
ax_vout.set_yticks(range(n_rows)); ax_vout.set_yticklabels([])
ax_vout.set_xticks([0]); ax_vout.set_xticklabels(['vout'], fontsize=8)
ax_vout.xaxis.set_label_position('top'); ax_vout.xaxis.tick_top()
ax_vout.set_yticks(np.arange(-0.5, n_rows, 1), minor=True)
ax_vout.grid(which='minor', color='white', linewidth=1.0)
ax_vout.tick_params(which='minor', length=0)
for k_idx, k in enumerate(ONES_COUNTS):
    v = vout_vec[k_idx]
    ax_vout.text(0, k_idx, f'{v:.1f}V', ha='center', va='center',
                 fontsize=7, fontweight='bold',
                 color='white' if v > 8 * VSTEP else '#333333')

# Colorbar for vout
cb = plt.colorbar(cm.ScalarMappable(norm=NORM_VOUT, cmap=CMAP_VOUT), cax=ax_cbar)
cb.set_label('vout (V)', fontsize=8)

out_png = OUT / 'visualize_dac_therm_16b.png'
fig.savefig(str(out_png), dpi=150, bbox_inches='tight')
plt.close(fig)
print(f"\nSaved: {out_png}")
