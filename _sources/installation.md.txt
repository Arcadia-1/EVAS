# Installation

## Requirements

- Python 3.9 or later
- NumPy and Matplotlib (installed automatically)

## From PyPI

```bash
pip install evas-sim
```

## From Source

```bash
git clone https://github.com/Arcadia-1/EVAS.git
cd EVAS
pip install -e ".[dev]"
```

## Verify

```bash
evas list
```

You should see the 5 bundled example groups printed.

## Engine Selection

The packaged default is EVAS2/Rust. Compatible Linux wheels include the
evas-rust shared library, and source installs build it with cargo unless
`EVAS_SKIP_RUST_CORE_BUILD=1` is set.

If your platform installed the pure Python wheel, build the Rust core from
source before using the default engine:

```bash
cargo build --manifest-path evas/rust_core/Cargo.toml --release
evas simulate path/to/tb.scs
```

Use the Python compatibility engine as an explicit fallback:

```bash
evas simulate path/to/tb.scs --engine python
```

You can also select the engine with `EVAS_ENGINE=python` or
`simulatorOptions options evas_engine=python`. The legacy `evas2` and `rust2`
selectors remain accepted as compatibility aliases for `evas-rust`.
