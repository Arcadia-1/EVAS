# Changelog

All notable changes to this project will be documented in this file.

## [0.1.2] — 2026-03-15

### Fixed
- `$strobe` / `$display` output now sorted strictly by simulation time across all module instances (previously grouped by instance instantiation order)

### Changed
- `comparator` example: delay panel now uses scatter plot (one point per CLK cycle) parsed from strobe log, replacing the misleading step-held waveform

## [0.1.1] — 2026-03-14

### Added
- Sphinx documentation with Apple Developer Documentation aesthetic (SF Pro fonts, `#0071e3` blue)
- Bilingual docs: English at root, Chinese at `/zh/`
- busuanzi visitor counter
- Streamlined project folder structure

### Added
- Initial public release on PyPI
- Event-driven Verilog-A simulator engine
- Spectre netlist parser
- `evas simulate` CLI command
- `evas run <name>` to run any of 17 bundled examples
- `evas list` to enumerate available examples
- 17 example circuits: clk_div, lfsr, cmp_strongarm, SAR ADC, and more
- Pure-Python implementation — no C compiler, no ngspice required
