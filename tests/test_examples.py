"""pytest test suite for EVAS examples.

Smoke tests: every example runs to completion and produces a non-empty CSV.
Functional tests: key signal properties validated against expected behaviour.

Testbench files live under tests/tb/ (copies of examples/ without the
user-facing analyze_*.py scripts).  The examples/ tree is for end-users only.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evas.netlist.runner import evas_simulate

TB       = Path(__file__).parent / "tb"
EXAMPLES = Path(__file__).parent.parent / "examples"


def _resolve(tb_rel: str) -> Path:
    """Return the testbench path, preferring examples/ over tests/tb/."""
    p = EXAMPLES / tb_rel
    if p.exists():
        return p
    return TB / tb_rel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simulate(tb_rel: str, tmp_path: Path) -> pd.DataFrame:
    """Run simulation and return the resulting tran.csv as a DataFrame."""
    tb = _resolve(tb_rel)
    assert tb.exists(), f"Testbench not found: {tb}"
    ok = evas_simulate(str(tb), output_dir=str(tmp_path))
    assert ok, f"evas_simulate returned False for {tb.name}"
    csv = tmp_path / "tran.csv"
    assert csv.exists(), "tran.csv was not created"
    return pd.read_csv(csv)


def _load_validate(tb_dir_name: str):
    """Import the validate_*.py module, checking examples/ then tests/tb/."""
    for base in (EXAMPLES, TB):
        candidates = list((base / tb_dir_name).glob("validate_*.py"))
        if candidates:
            spec = importlib.util.spec_from_file_location("_validate", candidates[0])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _run_validate(tmp_path: Path, tb_rel: str, fn_name: str, tb_dir: str):
    """Simulate and call a named validate function from a tb sub-directory."""
    _simulate(tb_rel, tmp_path)
    mod = _load_validate(tb_dir)
    assert mod is not None, f"No validate module found in tests/tb/{tb_dir}"
    fn = getattr(mod, fn_name)
    failures = fn(out_dir=tmp_path)
    assert failures == 0, f"{fn_name} reported {failures} failure(s)"


def _high(v: float, vth: float = 0.4) -> bool:
    return float(v) > vth


# ---------------------------------------------------------------------------
# Smoke tests — every example TB runs without errors
# ---------------------------------------------------------------------------

_SMOKE_CASES = [
    ("adc_ideal_8b",        "adc_ideal_8b/tb_adc_ideal_8b.scs"),
    ("clk_burst_gen",       "clk_burst_gen/tb_clk_burst_gen.scs"),
    ("clk_div",             "clk_div/tb_clk_div.scs"),
    ("cmp_offset_search",   "cmp_offset_search/tb_cmp_offset_search.scs"),
    ("cmp_strongarm",       "cmp_strongarm/tb_cmp_strongarm.scs"),
    ("d2b_4b",              "d2b_4b/tb_d2b_4b.scs"),
    ("dac_binary_8b",       "dac_binary_8b/tb_dac_binary_8b.scs"),
    ("dac_binary_clk_4b",   "dac_binary_clk_4b/tb_dac_binary_clk_4b.scs"),
    ("dac_onehot_inv_16b",  "dac_onehot_inv_16b/tb_dac_onehot_inv_16b.scs"),
    ("dac_therm_16b",       "dac_therm_16b/tb_dac_therm_16b.scs"),
    ("dac_weighted_11b",    "dac_weighted_11b/tb_dac_weighted_11b.scs"),
    ("digital_basics_and",  "digital_basics/tb_and_gate.scs"),
    ("digital_basics_or",   "digital_basics/tb_or_gate.scs"),
    ("digital_basics_not",  "digital_basics/tb_not_gate.scs"),
    ("digital_basics_dff",  "digital_basics/tb_dff_rst.scs"),
    ("dwa_ptr_gen",         "dwa_ptr_gen/tb_dwa_ptr_gen.scs"),
    ("edge_interval_timer", "edge_interval_timer/tb_edge_interval_timer.scs"),
    ("lfsr",                "lfsr/tb_lfsr.scs"),
    ("noise_gen",           "noise_gen/tb_noise_gen.scs"),
    ("ramp_gen",            "ramp_gen/tb_ramp_gen.scs"),
    ("sar_adc_4b",          "sar_adc_4b/tb_sar_adc_4b.scs"),
    ("sar_adc_weighted",    "sar_adc_weighted/tb_sar_adc_weighted.scs"),
]


@pytest.mark.parametrize("name,tb_rel", _SMOKE_CASES, ids=[c[0] for c in _SMOKE_CASES])
def test_smoke(tmp_path, name, tb_rel):
    """Every simulation runs without error and produces a non-empty CSV."""
    df = _simulate(tb_rel, tmp_path)
    assert len(df) > 0, "tran.csv is empty"
    sig_cols = [c for c in df.columns if c != "time"]
    assert len(sig_cols) > 0, "tran.csv has no signal columns"
    assert df[sig_cols].abs().max().max() > 0, "all signals are identically zero"


# ---------------------------------------------------------------------------
# Functional: digital_basics — truth-table and clocked-sequence checks
# ---------------------------------------------------------------------------

def test_and_gate(tmp_path):
    _run_validate(tmp_path, "digital_basics/tb_and_gate.scs", "validate_and", "digital_basics")


def test_or_gate(tmp_path):
    _run_validate(tmp_path, "digital_basics/tb_or_gate.scs", "validate_or", "digital_basics")


def test_not_gate(tmp_path):
    _run_validate(tmp_path, "digital_basics/tb_not_gate.scs", "validate_not", "digital_basics")


def test_dff_rst(tmp_path):
    _run_validate(tmp_path, "digital_basics/tb_dff_rst.scs", "validate_dff", "digital_basics")


# ---------------------------------------------------------------------------
# Functional: clock divider
# ---------------------------------------------------------------------------

def test_clk_div(tmp_path):
    _run_validate(tmp_path, "clk_div/tb_clk_div.scs", "validate_csv", "clk_div")


# ---------------------------------------------------------------------------
# Functional: adc_ideal_8b — 4-bit ideal ADC
# ---------------------------------------------------------------------------

def test_adc_ideal_8b(tmp_path):
    """dout_code sweeps 0..15 monotonically; rst_n reaches VDD."""
    df = _simulate("adc_ideal_8b/tb_adc_ideal_8b.scs", tmp_path)

    assert "dout_code" in df.columns, "dout_code missing from CSV"

    # rst_n must go high (normalized to ~1.00)
    assert df["rst_n"].max() > 0.5, "rst_n never went high"

    # After reset deassert, codes should be monotonically non-decreasing
    active = df[df["time"] > 40e-9]["dout_code"].values
    assert len(active) > 0
    diffs = np.diff(active.astype(float))
    assert np.all(diffs >= -1), "dout_code decreased unexpectedly (non-monotonic)"

    # 4-bit ADC: max code should reach 15
    assert df["dout_code"].max() >= 14, f"Max dout_code={df['dout_code'].max()}, expected 15"
    assert df["dout_code"].max() <= 15


# ---------------------------------------------------------------------------
# Functional: sar_adc_4b — 4-bit SAR ADC full sweep
# ---------------------------------------------------------------------------

def test_sar_adc_4b(tmp_path):
    """dout_code sweeps from 0 up to at least 12 during the vin ramp."""
    df = _simulate("sar_adc_4b/tb_sar_adc_4b.scs", tmp_path)

    assert "dout_code" in df.columns, "dout_code missing from CSV"
    assert df["dout_code"].min() == 0, "SAR ADC code never reached 0"
    assert df["dout_code"].max() >= 12, (
        f"SAR ADC max code={df['dout_code'].max()}, expected ≥ 12"
    )


# ---------------------------------------------------------------------------
# Functional: sar_adc_weighted — 12-bit non-binary SAR ADC
# ---------------------------------------------------------------------------

def test_sar_adc_weighted(tmp_path):
    """dout_code increases as vin ramps; reaches a high code near end."""
    df = _simulate("sar_adc_weighted/tb_sar_adc_weighted.scs", tmp_path)

    assert "dout_code" in df.columns, "dout_code missing from CSV"
    # vin ramps from 0 to VDD, so final code should be near full-scale
    assert df["dout_code"].max() > 0, "dout_code never left zero"


# ---------------------------------------------------------------------------
# Functional: dac_binary_8b — output voltage swing
# ---------------------------------------------------------------------------

def test_dac_binary_8b(tmp_path):
    _run_validate(tmp_path, "dac_binary_8b/tb_dac_binary_8b.scs", "validate_csv", "dac_binary_8b")


# ---------------------------------------------------------------------------
# Functional: ramp_gen — output is a ramp signal
# ---------------------------------------------------------------------------

def test_ramp_gen(tmp_path):
    _run_validate(tmp_path, "ramp_gen/tb_ramp_gen.scs", "validate_csv", "ramp_gen")


# ---------------------------------------------------------------------------
# Functional: lfsr — output toggles (non-trivial bit sequence)
# ---------------------------------------------------------------------------

def test_lfsr(tmp_path):
    """LFSR output transitions at least once."""
    df = _simulate("lfsr/tb_lfsr.scs", tmp_path)

    # Find any output signal other than reset/clock inputs
    bit_cols = [c for c in df.columns if c not in ("time", "rstb", "clk")]
    assert len(bit_cols) > 0, "No output signals in LFSR CSV"

    # At least one output should toggle
    toggled = any(df[c].nunique() > 1 for c in bit_cols)
    assert toggled, "No LFSR output ever changed value"


# ---------------------------------------------------------------------------
# Functional: noise_gen — output is non-deterministic (has spread)
# ---------------------------------------------------------------------------

def test_noise_gen(tmp_path):
    _run_validate(tmp_path, "noise_gen/tb_noise_gen.scs", "validate_csv", "noise_gen")
