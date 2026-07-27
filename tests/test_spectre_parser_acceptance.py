import textwrap

import pytest

from evas.netlist.spectre_parser import parse_spectre


def _parse_netlist(tmp_path, body):
    scs = tmp_path / "input.scs"
    scs.write_text(textwrap.dedent(body), encoding="utf-8")
    return parse_spectre(str(scs))


def test_pwl_wave_parenthesized_expression_with_spaces(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        global 0
        V1 out 0 vsource type=pwl wave=[0s 0.0 (10n + 100p) 1.0]
        tran tran stop=20n
        """,
    )

    assert len(netlist.sources) == 1
    assert netlist.sources[0].params["wave"] == pytest.approx(
        [0.0, 0.0, 10.1e-9, 1.0]
    )


@pytest.mark.parametrize(
    "wave",
    [
        "0 0 (10n + ) 1",
        "0 0 (10n + 100p)) 1",
    ],
)
def test_pwl_wave_rejects_incomplete_or_unbalanced_expression(tmp_path, wave):
    with pytest.raises(ValueError, match="Invalid PWL wave token|unmatched"):
        _parse_netlist(
            tmp_path,
            f"""\
            simulator lang=spectre
            global 0
            V1 out 0 vsource type=pwl wave=[{wave}]
            tran tran stop=20n
            """,
        )


def test_multiline_pwl_wave_without_continuation_marker_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"multiline wave=\[\.\.\.\] requires"):
        _parse_netlist(
            tmp_path,
            """\
            simulator lang=spectre
            global 0
            V1 out 0 vsource type=pwl wave=[
                0 0
                10n 1
            ]
            tran tran stop=20n
            """,
        )


def test_source_line_rejects_mixed_backslash_and_plus_continuation(tmp_path):
    with pytest.raises(ValueError, match=r"cannot mix backslash and '\+'"):
        _parse_netlist(
            tmp_path,
            """\
            simulator lang=spectre
            global 0
            V1 (out 0) vsource type=pwl \\
            + wave=[0 0 10n 1]
            tran tran stop=20n
            """,
        )


def test_plus_continued_pwl_rejects_standalone_closing_bracket(tmp_path):
    with pytest.raises(ValueError, match="closing bracket"):
        _parse_netlist(
            tmp_path,
            """\
            simulator lang=spectre
            global 0
            V1 (out 0) vsource type=pwl wave=[
            + 0 0
            + 10n 1
            ]
            tran tran stop=20n
            """,
        )


@pytest.mark.parametrize(
    "source_lines",
    [
        "V1 (out 0) vsource type=pwl\n+ wave=[0 0 10n 1]",
        "V1 (out 0) vsource type=pwl \\\nwave=[0 0 10n 1]",
    ],
)
def test_source_line_accepts_single_spectre_continuation_style(
    tmp_path,
    source_lines,
):
    netlist = _parse_netlist(
        tmp_path,
        f"""\
        simulator lang=spectre
        global 0
        {source_lines}
        tran tran stop=20n
        """,
    )

    assert netlist.sources[0].params["wave"] == pytest.approx(
        [0.0, 0.0, 10e-9, 1.0]
    )


def test_parenthesized_simulator_directive_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"simulator language directive"):
        _parse_netlist(
            tmp_path,
            """\
            simulator( lang=spectre )
            global 0
            V1 (out 0) vsource dc=1
            tran tran stop=1n
            """,
        )


def test_named_transient_analysis_is_accepted(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        tran1 tran stop=1n maxstep=10p
        """,
    )

    assert netlist.tran is not None
    assert netlist.tran.name == "tran1"
    assert netlist.tran.stop == pytest.approx(1e-9)


def test_options_analysis_statement_is_accepted(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        options options reltol=1e-6 save=lvlpub
        """,
    )

    assert netlist.simulator_options["reltol"] == pytest.approx(1e-6)
    assert netlist.simulator_options["save"] == "lvlpub"


def test_bare_two_terminal_resistor_is_accepted(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        R_rst rst 0 resistor r=1G
        """,
    )

    assert len(netlist.instances) == 1
    assert netlist.instances[0].name == "R_rst"
    assert netlist.instances[0].nodes == ["rst", "0"]
    assert netlist.instances[0].model_name == "resistor"
    assert netlist.instances[0].params["r"] == pytest.approx(1e9)


def test_bare_tran_output_filename_is_accepted(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        tran tran stop=1n write=spectre.raw
        """,
    )

    assert netlist.tran is not None
    assert netlist.tran.stop == pytest.approx(1e-9)


def test_source_instance_may_have_same_name_as_primitive(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        vsource (vin 0) vsource dc=0.9
        """,
    )

    assert netlist.sources[0].name == "vsource"
    assert netlist.sources[0].params["dc"] == pytest.approx(0.9)


def test_duplicate_source_instance_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate instance name"):
        _parse_netlist(
            tmp_path,
            """\
            simulator lang=spectre
            vsource (vin 0) vsource dc=0.9
            vsource (clk 0) vsource dc=0.0
            """,
        )


def test_source_keyword_instance_with_quoted_component_name_is_accepted(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        vsource "vin_src" (vin 0) vsource dc=0.9
        """,
    )

    assert netlist.sources[0].name == "vin_src"
    assert netlist.sources[0].node_pos == "vin"
    assert netlist.sources[0].params["dc"] == pytest.approx(0.9)


def test_redundant_source_keyword_form_reuses_primitive_instance_name(tmp_path):
    with pytest.raises(ValueError, match="duplicate instance name 'vsource'"):
        _parse_netlist(
            tmp_path,
            """\
            simulator lang=spectre
            vsource vclk (clk 0) vsource dc=0
            vsource vrst (rst 0) vsource dc=0.9
            """,
        )


@pytest.mark.parametrize(
    "line",
    [
        "include models.scs",
        "ahdl_include model.va",
    ],
)
def test_include_statements_require_quoted_paths(tmp_path, line):
    with pytest.raises(ValueError, match="quoted path"):
        _parse_netlist(
            tmp_path,
            f"""\
            simulator lang=spectre
            {line}
            """,
        )


@pytest.mark.parametrize(
    "line",
    [
        "tranfoo stop=1n",
        "savejunk out",
        "parametersExtra gain=2",
    ],
)
def test_keyword_prefixes_are_not_valid_statements(tmp_path, line):
    with pytest.raises(ValueError, match="Unsupported or malformed Spectre statement"):
        _parse_netlist(
            tmp_path,
            f"""\
            simulator lang=spectre
            global 0
            {line}
            """,
        )


def test_bare_isource_uses_primitive_token_not_parameter_text(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        global 0
        I1 in 0 isource type=dc file=vsource.tbl dc=1u
        tran tran stop=1n
        """,
    )

    assert len(netlist.sources) == 1
    assert netlist.sources[0].kind == "current"
    assert netlist.sources[0].params["dc"] == pytest.approx(1e-6)


def test_parameters_accept_spaced_parenthesized_static_expression(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        global 0
        parameters base=10n td=(base + 100p) gain=2
        V1 (out 0) vsource dc=td
        """,
    )

    assert netlist.parameters == pytest.approx(
        {"base": 10e-9, "td": 10.1e-9, "gain": 2.0}
    )
    assert netlist.sources[0].params["dc"] == pytest.approx(10.1e-9)


def test_parameters_accept_multiple_dependent_static_expressions(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        global 0
        parameters a=1n b=(a + 2p) c=(b * 2)
        V1 (out 0) vsource dc=c
        """,
    )

    assert netlist.parameters == pytest.approx(
        {"a": 1e-9, "b": 1.002e-9, "c": 2.004e-9}
    )

@pytest.mark.parametrize(
    "declaration",
    [
        "parameters td=(1 + )",
        "parameters td=(unknown + 1n)",
        "parameters td=bad+",
        "parameters td=(1 + 2",
    ],
)
def test_parameters_reject_malformed_or_unresolvable_expression(
    tmp_path,
    declaration,
):
    with pytest.raises(ValueError, match="Invalid Spectre parameter"):
        _parse_netlist(
            tmp_path,
            f"""\
            simulator lang=spectre
            global 0
            {declaration}
            V1 (out 0) vsource dc=td
            """,
        )


def test_instance_parameters_accept_spaced_assignments(tmp_path):
    netlist = _parse_netlist(
        tmp_path,
        """\
        simulator lang=spectre
        global 0
        XU1 (in out) buf gain = 3 offset = 1
        """,
    )

    assert netlist.instances
    params = netlist.instances[0].params
    assert params["gain"] == 3
    assert params["offset"] == 1


@pytest.mark.parametrize(
    "assignment",
    [
        "gain=",
        "gain =",
        "gain = offset=1",
    ],
)
def test_instance_parameter_missing_value_is_rejected(tmp_path, assignment):
    with pytest.raises(ValueError, match="Missing value.*gain"):
        _parse_netlist(
            tmp_path,
            f"""\
            simulator lang=spectre
            global 0
            XU1 (in out) buf {assignment}
            """,
        )
