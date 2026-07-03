# Support Tiers

EVAS is an event-driven voltage-domain simulator. Benchmark reports should use
the support tiers below instead of treating every Verilog-A/AMS row as one flat
support target.

Unsupported-feature diagnostics include a stable `[support-tier: <name>]`
suffix in text output and a `support_tier` field in JSON lint output.

| Tier | Boundary | Benchmark interpretation |
|------|----------|--------------------------|
| `behavioral-event` | Voltage-domain behavioral transient models: `V(...)` reads/drives, `cross`/`above`/`timer`/`initial_step`/`final_step`, `transition`, state machines, control logic, table/random/file helpers. This is the current EVAS core strength. | A valid failure here is a supported EVAS bug unless a narrower diagnostic says otherwise. |
| `behavioral-continuous-time` | Legal voltage-domain `ddt`, `idt`, `idtmod`, `laplace_*`, `zi_*`, and `limexp` style models. EVAS has behavioral transient approximations for selected forms, but this is not a Spectre-equivalent continuous-time solver claim. | Treat unsupported or inaccurate rows as planned/limited continuous-time work, not as AMS/KCL coverage. |
| `ams-digital` | `wreal`, `logic`, `always`, continuous `assign`, packed logic vectors, `specify`/`specparam`, `connectmodule`, and `connectrules`. EVAS supports only small behavioral bridge subsets where documented; a full AMS/digital event kernel is outside the certified core. | Full AMS/digital failures are outside current benchmark certification unless the row is explicitly documented as a supported bridge subset. |
| `conservative-current-kcl` | `I(...) <+ ...`, branch currents, current probes, indirect branch equations, charge/current branch contributions, and KCL/MNA topology solving. | Architectural roadmap item, not a parser bug and not part of current certified EVAS support. |

Current EVAS benchmark PASS claims apply to `behavioral-event` designs, plus
explicitly documented behavioral helper subsets. Rows that require full
`ams-digital` or `conservative-current-kcl` semantics should be reported
separately from supported EVAS bugs.

## Diagnostic Taxonomy

`evas lint` and the optional `evas simulate --ahdllint` preflight expose the
same taxonomy. Unsupported current contributions and current probes report
`support_tier="conservative-current-kcl"`. Unsupported digital procedural syntax
reports `support_tier="ams-digital"` when it reaches the parser. Unknown
unregistered function/operator calls report `support_tier="outside-current-scope"`
so benchmark tooling can separate truly unclassified constructs from the four
documented support tiers.
