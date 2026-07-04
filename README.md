# EVAS — Event-driven Verilog-A Simulator

[![PyPI](https://img.shields.io/pypi/v/evas-sim.svg)](https://pypi.org/project/evas-sim/)
[![CI](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml/badge.svg)](https://github.com/Arcadia-1/EVAS/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-evas.tokenzhang.com-blue)](https://evas.tokenzhang.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight behavioral simulator for digital/mixed-signal Verilog-A models.
EVAS runs supported event-driven designs through the EVAS2/Rust backend by default, with an explicit Python compatibility fallback. No ngspice, no KCL/KVL solver.


---

Docs: [evas.tokenzhang.com](https://evas.tokenzhang.com)

---

## What EVAS does

EVAS simulates **voltage-mode, event-driven** Verilog-A behavioral models. You provide:

1. **A `.va` file** — your behavioral model (comparator, DAC, SAR logic, DWA controller, …)
2. **A `.scs` testbench netlist** — voltage sources, `ahdl_include`, and a `tran` statement
3. **Run `evas simulate`** — get `tran.csv` waveforms and optional plots

The main simulator is transient-first. Compiled models also expose lightweight
behavioral helpers for `analysis("ac")`, `ac_stim()`, and Verilog-A noise source
functions so source-style models can run AC/noise sweeps from Python without
claiming SPICE-style linearized circuit analysis.

The bundled examples are a compact smoke-test set. For your own design, copy the
closest example directory, swap in your `.va`, adjust the stimulus sources and
`save` list, and run. The larger verification example library belongs with the
agent workflow in `veriloga-skills/evas-sim`.

## Installation

```bash
pip install evas-sim
evas list        # verify install — prints bundled example groups
```

If `evas` is not on PATH, use `python -m evas`.

The packaged default is EVAS2/Rust. Compatible Linux wheels include the
`evas-rust` shared library, and source installs build it with cargo unless
`EVAS_SKIP_RUST_CORE_BUILD=1` is set. If your platform installed the pure Python
wheel or the Rust backend is unavailable, select the Python compatibility engine
explicitly with `--engine python`, `EVAS_ENGINE=python`, or
`simulatorOptions options evas_engine=python`. The legacy `evas2` and `rust2`
selectors remain accepted as compatibility aliases for `evas-rust`.

## Simulating your own design

```bash
evas simulate path/to/tb.scs -o output/mydesign
evas simulate path/to/tb.scs -o output/mydesign --engine python
evas simulate path/to/tb.scs -o output/mydesign --ahdllint
evas simulate path/to/tb.scs -o output/mydesign --spectre-strict
```

Output in `-o` dir: `tran.csv` (waveforms), `strobe.txt` (log messages), `.png` plots.
`--ahdllint` runs EVAS lint as a non-blocking simulation preflight and writes
diagnostics into the simulation log before model compilation. Netlists may also
request this with `simulatorOptions options ahdllint=true`.
`--spectre-strict` runs the same lint preflight in blocking strict standalone
Spectre mode, rejecting EVAS extension syntax before compilation. Netlists may
also request this with `simulatorOptions options spectre_strict=true`.

Before a full simulation, you can run a Spectre/AHDL-style static lint pass:

```bash
evas lint path/to/model.va
evas lint path/to/tb.scs --format json
evas lint path/to/model.va --spectre-strict
```

`evas lint` follows `ahdl_include` statements in `.scs` files and reports two
classes of issues: `compat-error` for EVAS/Spectre subset problems that should
block a candidate, and warning diagnostics for AHDL-style modeling risks such as
discrete signals directly driving analog contributions or suspicious transition
usage. Lint warnings do not change simulation pass/fail status.
Current warning coverage includes a Cadence AHDL-inspired subset for
transition timing and simple continuous-input dataflow, conditional potential
contributions, case defaults, exact branch equality tests, floor/ceil
contribution discontinuities, `gnd` node portability, discrete function
arguments, implicit integer casts, and simulator-stop tasks inside loops.
Each lint diagnostic is backed by a small rule registry that records its code,
severity, canonical rule name, phase, source category, and related
Cadence/Spectre identifiers when known.
Diagnostics produced from parsed Verilog-A nodes include source line and column
coordinates when available, so repair tools can point users back to the
triggering statement or expression.
Compatibility diagnostics include Cadence/Spectre-aligned cases such as
conditionally executed analog operators (`transition`, `slew`, `idt`) and
discipline vector ranges that depend on runtime variables instead of numeric or
parameter constant expressions.
Strict Spectre mode additionally rejects EVAS extension syntax that standalone
Spectre rejects in the current compatibility target, including AMS bridge
constructs (`logic`, `wreal`, continuous `assign`, `always`), task/do-while
extensions, runtime electrical-node indexing, selected version-gated random
distributions, seeded `$rdist_*` distributions whose Spectre PRNG sequence
parity is not certified, integer bit/part-select concatenation gaps,
`generate`, `specify`, `connectmodule`, and `connectrules`.
The lint regression suite also keeps a small set of public oracle fixtures under
`tests/fixtures/lint_oracle_cases`. These cases record distilled expected EVAS
diagnostic codes only; raw Cadence/Spectre logs and generated certification
reports are not committed.

**Minimal testbench template** (`.scs`):

```spectre
simulator lang=spectre
global 0

ahdl_include "my_module.va"

Vvdd (vdd 0) vsource type=dc dc=1.8
Vclk (clk 0) vsource type=pulse val0=0 val1=1.8 period=10n rise=0.1n fall=0.1n width=4.9n

IDUT (clk vdd out) my_module vdd=1.8

tran tran stop=200n maxstep=0.1n
save clk:2e out:6f
```

### Testbench structure reference

The bundled examples contain five small groups and their testbenches. They are
the in-package reference for common wiring patterns:

| Pattern needed | Look at |
|---------------|---------|
| Clocked digital logic | `clk_div`, `digital_basics` |
| Comparator with feedback | `comparator/cmp_offset_search` |
| ADC + sample-hold | `adc_dac_ideal_4b` |
| Noise / random stimulus | `noise_gen` |
| Multi-cycle edge timing | `comparator/cmp_delay`, `edge_interval_timer` |

## Supported Verilog-A

### Support tiers

EVAS reports unsupported-feature diagnostics with a stable
`[support-tier: <name>]` suffix in text output and a `support_tier` field in
JSON lint output. Benchmark reports should use these tiers instead of treating
all Verilog-A/AMS failures as one flat simulator target.

| Tier | Boundary | Benchmark interpretation |
|------|----------|--------------------------|
| `behavioral-event` | Voltage-domain behavioral transient models: `V(...)` reads/drives, `cross`/`above`/`timer`/`initial_step`/`final_step`, `transition`, state machines, control logic, table/random/file helpers. This is the current EVAS core strength. | A valid failure here is a supported EVAS bug unless a narrower diagnostic says otherwise. |
| `behavioral-continuous-time` | Legal voltage-domain `ddt`, `idt`, `idtmod`, `laplace_*`, `zi_*`, and `limexp` style models. EVAS has behavioral transient approximations for selected forms, but this is not a claim of Spectre-equivalent continuous-time transfer-function solving. | Treat unsupported or inaccurate rows as planned/limited continuous-time work, not as AMS/KCL coverage. |
| `ams-digital` | `wreal`, `logic`, `always`, continuous `assign`, packed logic vectors, `specify`/`specparam`, `connectmodule`, and `connectrules`. EVAS supports only small behavioral bridge subsets where documented; a full AMS/digital event kernel is outside the certified core. | Full AMS/digital failures are outside current benchmark certification unless the row is explicitly documented as a supported bridge subset. |
| `conservative-current-kcl` | `I(...) <+ ...`, branch currents, current probes, indirect branch equations, charge/current branch contributions, and KCL/MNA topology solving. | Architectural roadmap item, not a parser bug and not part of current certified EVAS support. |

Certification boundary: current EVAS benchmark PASS claims apply to
`behavioral-event` designs, plus explicitly documented behavioral helper subsets.
Rows that require full `ams-digital` or `conservative-current-kcl` semantics must
be reported separately from supported EVAS bugs.

Continuous-time policy: `ddt()`, `idt()`, `idtmod()`, `laplace_nd()`,
`laplace_np()`, `laplace_zd()`, `laplace_zp()`, `zi_nd()`, `zi_np()`,
`zi_zd()`, `zi_zp()`, and `limexp()` are supported as voltage-domain
behavioral transient approximations in ordinary analog statements. EVAS rejects
Spectre-illegal conditional/event-body placements through lint or compile-time
diagnostics. Unsupported continuous-time operators such as `absdelay()` are
reported as `behavioral-continuous-time`, not as generic parser gaps.

Noise and stochastic policy: in ordinary transient analysis,
`white_noise()`, `flicker_noise()`, and `noise_table()` contribute zero
instantaneous random voltage and expose their PSD through `evaluate_noise()`,
`noise_spectrum()`, and `integrated_noise()`. Explicit `$random`, `$dist_*()`,
and `$rdist_*()` calls produce deterministic fixed-seed behavioral draws; the
same seed and draw order reproduce the same sequence. Supported distribution
helpers include uniform, normal, exponential, poisson, chi-square, t, and
erlang forms. Unsupported distribution names produce an
`EVAS-COMP-EUNSUPPORTED` diagnostic in the `behavioral-event` tier.

Subprogram policy: EVAS supports Spectre-style old-form functions/tasks
(`input x; real x;`), ANSI-style task/function arguments, local variables,
multi-argument calls, bounded recursion, and task/function calls from event
bodies with normal state-update ordering.

Vector and indexing policy: pure expression bit-select, part-select,
concatenation, replication, reduction, packed logic vectors, and integer/real
state arrays are separate from electrical topology. Static `generate`/`genvar`
elaboration is supported for a limited behavioral/continuous-assign subset.
Runtime voltage-domain electrical indexing such as `V(bus[i])` and
`V(bus[i]) <+ ...` is supported by dynamic node resolution; dynamic current
indexing remains part of the unsupported `conservative-current-kcl` tier.

| Feature | Status |
|---------|--------|
| `V(node) <+`, `V(a,b)` differential | ✅ |
| `@(cross(...))`, `@(above(...))`, `@(initial_step)` | ✅ |
| `cross(expr, dir, time_tol, expr_tol)` event tolerances | ✅ (behavioral approximation) |
| `@(timer(period))`, `@(final_step)` | ✅ |
| `transition()` with delay / rise / fall | ✅ |
| `slew(x, maxrise, maxfall)` transient limiter | ✅ (behavioral approximation) |
| `for`, `repeat`, `while`, `do while`, `if/else`, `case/endcase`, `begin/end` | ✅ |
| arrays, including integer/real 1-D and 2-D state arrays | ✅ |
| bit/part select, concat, replication, reduction, packed logic vectors | ✅ |
| runtime voltage electrical indexing such as `V(bus[i])` | ✅ |
| `branch (p,n) br; V(br)` named branch voltage probes | ✅ |
| `$analog_node_alias()` and string OOMR voltage probes such as `V(sigpath)` | ✅ |
| `$table_model()` 1-D file tables and simple 2-D array-backed surfaces | ✅ |
| parameters and variables (real / integer / string) | ✅ |
| user-defined functions/tasks, including bounded recursive functions | ✅ |
| `module`, `connectmodule`, simple behavioral hierarchy | ✅ |
| `logic`, `wreal`, simple continuous `assign`, simple `always @(posedge/negedge ...)` | limited `ams-digital` bridge subset |
| simple `specify` / `specparam` path delay on behavioral assignments | limited `ams-digital` bridge subset |
| `` `include ``, `` `define ``, `` `default_transition `` | ✅ |
| SI suffixes, math: `sin` `cos` `exp` `ln` `log` `pow` `floor` `ceil` … | ✅ |
| `$temperature`, `$vt`, `$abstime` | ✅ |
| `$bound_step()` | ✅ |
| `$fopen()`, `$fclose()`, `$fscanf()`, `$fstrobe()`, `$fwrite()`, `$fdisplay()` | ✅ |
| `$display`, `$strobe`, `$sformat()`, `$swrite()` | ✅ |
| `$random`, `$dist_*()`, `$rdist_*()` behavioral random helpers | ✅ |
| `last_crossing(expr, dir, time_tol, expr_tol)` | ✅ (most-recent event-time approximation) |
| `analysis("ac")`, `ac_stim()` | ✅ (behavioral Python sweep helper) |
| `white_noise()`, `flicker_noise()`, `noise_table()` | ✅ (behavioral PSD / integrated-noise helper) |
| `ddt()`, `idt()`, `laplace_*()`, `zi_*()`, `limexp()` | limited `behavioral-continuous-time` approximation |
| `generate` / `genvar` | limited static-elaboration subset |
| `connectrules` | unsupported `ams-digital` scope |
| custom `nature` / `discipline` semantics beyond the bundled VAMS stubs | not supported by design |
| analog primitive instances such as `resistor` / `isource` | not supported by design |
| `I() <+`, `q() <+`, branch charge/current contributions, current probes | unsupported `conservative-current-kcl` scope |
| SPICE-style AC/DC matrix solving, transistors | unsupported `conservative-current-kcl` scope |
| Spectre `subckt` hierarchy | not yet implemented |

### Accuracy Profiles

You can set `simulatorOptions options evas_profile=<mode>` in `.scs`:

- `fast`: lower refinement (`refine_factor=8`, `refine_steps=4`) for faster runtime
- `balanced`: default EVAS behavior (`16`, `8`)
- `precision`: higher refinement (`32`, `16`) for tighter event/cross timing

`errpreset=conservative/liberal` is still respected; `evas_profile` applies an explicit EVAS-side override when set.
Practical correspondence: `fast ≈ liberal`, `balanced ≈ moderate`, `precision ≈ conservative` (guidance only, not solver-equivalence claim).

## CSV output format

The `save` statement accepts per-signal format hints:

```
save vin:10e vout:6f clk:2e dout:d
```

| Suffix | Example |
|--------|---------|
| `:6e` (default) | `4.500000e-01` |
| `:Nf` | fixed-point, N decimal places |
| `:d` | integer (for digital buses) |

## Bundled examples (reference only)

Five groups ship with the PyPI package for install verification, CLI sanity
checks, and small starting templates. The full workflow-oriented example set is
maintained outside this simulator package in `veriloga-skills/evas-sim`.

| Group | Verilog-A modules | Notes |
|-------|------------------|-------|
| `clk_div` | `clk_div` | |
| `digital_basics` | `and_gate`, `or_gate`, `not_gate`, `dff_rst`, `inverter` | |
| `noise_gen` | `noise_gen` | |
| `adc_dac_ideal_4b` | `adc_ideal_4b`, `dac_ideal_4b`, `sh_ideal` | 3 stimuli: ramp / sine / 1000-pt sine |
| `comparator` | `cmp_ideal`, `cmp_strongarm`, `cmp_offset_search`, `cmp_delay`, `edge_interval_timer` | 4 sub-examples |

## Contributing

```bash
git clone https://github.com/Arcadia-1/EVAS.git
cd EVAS
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
