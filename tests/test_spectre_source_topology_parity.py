from __future__ import annotations

import csv
import textwrap

import pytest

from evas.netlist.runner import evas_simulate
from evas.netlist.spectre_parser import parse_spectre


def test_redundant_source_keyword_form_matches_spectre_instance_and_polarity(
    tmp_path,
):
    scs = tmp_path / "master_first_sources.scs"
    scs.write_text(
        textwrap.dedent(
            """
            simulator lang=spectre
            tran tran stop=1n step=1n
            vsource Vsig (sig 0) vsource dc=0.2
            save sig
            """
        ),
        encoding="utf-8",
    )

    netlist = parse_spectre(str(scs))

    # Cadence treats the leading primitive as the instance identity in this
    # redundant spelling and reverses the terminal orientation.  The middle
    # token is not a replacement instance name.
    assert [src.name for src in netlist.sources] == ["vsource"]
    assert [(src.node_pos, src.node_neg) for src in netlist.sources] == [
        ("0", "sig"),
    ]

    out_dir = tmp_path / "redundant_out"
    assert evas_simulate(str(scs), output_dir=str(out_dir), spectre_strict=True)
    rows = list(csv.DictReader((out_dir / "tran.csv").open(encoding="utf-8")))
    assert float(rows[0]["sig"]) == pytest.approx(-0.2)


def test_voltage_source_negative_terminal_offsets_positive_node(tmp_path):
    scs = tmp_path / "floating_negative_terminal.scs"
    out_dir = tmp_path / "out"
    scs.write_text(
        textwrap.dedent(
            """
            simulator lang=spectre
            tran tran stop=1n step=1n
            Vcm (vcm 0) vsource dc=0.4
            Vsig (sig vcm) vsource dc=0.2
            save vcm sig
            """
        ),
        encoding="utf-8",
    )

    assert evas_simulate(str(scs), output_dir=str(out_dir), spectre_strict=True)

    rows = list(csv.DictReader((out_dir / "tran.csv").open(encoding="utf-8")))
    assert rows
    first = rows[0]
    assert float(first["vcm"]) == pytest.approx(0.4)
    assert float(first["sig"]) == pytest.approx(0.6)


def test_floating_voltage_source_island_is_anchored_like_spectre_gmin(tmp_path):
    scs = tmp_path / "floating_source_island.scs"
    out_dir = tmp_path / "out"
    log_path = tmp_path / "evas.log"
    scs.write_text(
        textwrap.dedent(
            """
            simulator lang=spectre
            tran tran stop=1n step=1n
            Vclk (clk vss) vsource dc=0.9
            Ven (enable vss) vsource dc=0.4
            save clk enable
            """
        ),
        encoding="utf-8",
    )

    assert evas_simulate(
        str(scs),
        output_dir=str(out_dir),
        log_path=str(log_path),
        spectre_strict=True,
    )

    log_text = log_path.read_text()
    assert "anchoring the floating source island" in log_text
    rows = list(csv.DictReader((out_dir / "tran.csv").open(encoding="utf-8")))
    assert float(rows[0]["clk"]) == pytest.approx(0.9)
    assert float(rows[0]["enable"]) == pytest.approx(0.4)
