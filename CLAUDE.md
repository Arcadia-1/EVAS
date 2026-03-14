# EVAS — Project Instructions for Claude

## Analyze scripts (analyze_*.py) conventions

- **Wall-clock time**: measure with `time.perf_counter()` around `evas_simulate()`. Display in plot titles as `wall clock: X.XXXX s` (4 decimal places, seconds).
- **X-axis range**: call `ax.set_xlim(t[0], t[-1])` to eliminate blank margins. With `sharex=True` one call covers all panels; without it call on every time-axis panel. Non-time-axis plots (scatter, histogram) are exempt.
- **Y-axis range**: voltage signals → `[-0.1·VDD, 1.2·VDD]`. Non-voltage axes (integer codes, stacked bits, delay in ps, log-scale panels, noise/zoom panels) keep their natural range — do not apply VDD scaling mechanically.
- **Output path**: `_DEFAULT_OUT = HERE.parent.parent.parent / 'output' / '<name>'` (three `.parent` steps from `evas/examples/<name>/` reaches repo root). Do NOT use `os.environ` — `evas run` calls `analyze(output_dir)` directly (cli.py line 122).
- **Multiple plots**: prefer one plot per configuration rather than a combined figure, unless asked otherwise.
- **Plot title**: include signal-level info and wall-clock time.

## Test conventions

- Unit tests: `tests/test_engine.py` (engine), `tests/test_compiler.py` (lexer+parser), `tests/test_examples.py` (functional end-to-end).
- Functional tests call `validate_csv()` from `evas/examples/<name>/validate_*.py`.
- No smoke tests — functional tests already cover simulation.
- Use `tmp_path` fixture for output isolation (parallel-safe).

## Docs

- Two Sphinx docs: `docs_zh/` (Chinese, at `/zh/`) and `docs_en/` (English, at root).
- Custom CSS: Apple Developer Documentation aesthetic — SF Pro fonts, `#0071e3` blue, white/charcoal.
- busuanzi CDN: `https://cdn.bootcdn.net/ajax/libs/busuanzi/2.3.0/bsz.pure.mini.js`.
- CI: `.github/workflows/docs.yml` deploys to `evas.tokenzhang.com` on push to `main`.
