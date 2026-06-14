## Stat Arb Execution Engine

Execution-first repo for an equity stat arb baseline, built paper-trading-first.

### Current state

The repo now has two usable paths:

1. `scripts/run_paper_baseline.py`
Offline baseline smoke runner with local books, pair logic, paper execution, portfolio aggregation, audit logs, and either synthetic fixtures or IBKR daily closes.

2. `scripts/stream_zscore.py`
Live-oriented IBKR loop based on the older pair config flow.

The baseline execution layer reuses the existing OMS/paper core:
- `engine/execution_engine.py`
- `engine/event_logger.py`
- `engine/portfolio_tracker.py`

### Repo structure

- `engine/baseline/`
  Local-book baseline models, config loading, portfolio allocation, and synthetic market fixtures.
- `config/paper_baseline_books.json`
  Legacy monolithic example config for 4 local books.
- `config/paper_baseline_books/`
  Preferred scalable config layout with `defaults.json`, one file per book, and a `research/` sidecar for stats.
- `config/pairs.json`
  Legacy pair config used by the live-oriented path.
- `scripts/run_paper_baseline.py`
  Main smoke path for the baseline execution slice.
- `scripts/build_baseline_config.py`
  Converts a legacy `pairs.json`-style config into the baseline books format.
- `tests/test_paper_baseline.py`
  Regression coverage for baseline signal flow and audit outputs.

### Quickstart

Create a virtual environment, then run:

```powershell
uv run pytest -q
python -m unittest discover -s tests -v
python scripts\run_paper_baseline.py --days 30
```

To run the same baseline with daily closes fetched from IBKR:

```powershell
python scripts\run_paper_baseline.py --market-source ibkr --days 60 --ib-host 127.0.0.1 --ib-port 4002 --ib-client-id 21
```

### Baseline smoke outputs

The smoke runner writes append-only audit logs into `logs/paper_baseline_smoke/`:

- `signals.csv`
- `decisions.csv`
- `orders.csv`
- `trades.csv`
- `positions.csv`
- `exposures.csv`
- `state_latest.json`
- `state_snapshot.jsonl`
- `equity_curve.csv`
- `source_daily_closes.json` when `--market-source ibkr`

These files are enough to reconstruct:
- why a pair was eligible or blocked
- which threshold triggered entry or exit
- what paper order intents were generated
- what gross and net exposures were active

`source_daily_closes.json` captures the exact daily close inputs used for an IBKR-backed run.

### Config modes

`load_baseline_config(...)` now supports three inputs:

1. Baseline books config
   Example: `config/paper_baseline_books.json`

2. Baseline config directory
   Example: `config/paper_baseline_books/`

3. Legacy pairs config
   Example: `config/pairs.json`

If you point the baseline loader at the legacy format, it automatically adapts supported local pairs into books by exchange mapping:

- `SBF -> france`
- `IBIS -> germany`
- `AEB -> netherlands`
- `SFB -> sweden`

Cross-country pairs and unsupported exchanges are skipped in that compatibility path.

### Research stats sidecar

The preferred directory layout separates structure from research metrics:

- `config/paper_baseline_books/books/*.json`
  universe, pairs, fixtures, thresholds
- `config/paper_baseline_books/research/pair_stats.sample.json`
  correlation, ADF, Engle-Granger, half-life, readiness notes
- `config/paper_baseline_books/research/pair_stats.sample.csv`
  same payload in flat-file form for easier research exports

You can run the smoke with an explicit sidecar:

```powershell
python scripts\run_paper_baseline.py --config config\paper_baseline_books --stats config\paper_baseline_books\research\pair_stats.sample.json --days 30
```

CSV sidecars are also supported:

```powershell
python scripts\run_paper_baseline.py --config config\paper_baseline_books --stats config\paper_baseline_books\research\pair_stats.sample.csv --days 30
```

To generate a blank template for a new research export:

```powershell
python scripts\build_pair_stats_template.py --config config\paper_baseline_books
```

### IBKR Daily Close Mode

`run_paper_baseline.py` now supports `--market-source ibkr` in addition to the synthetic default.

- It qualifies one stock contract per symbol via IBKR.
- It requests `1 day` historical bars and uses the `close` field.
- It aligns the baseline on the intersection of daily sessions available across all symbols in the run.

Useful flags:

- `--ib-host`, `--ib-port`, `--ib-client-id`
- `--ib-connect-timeout-sec`
- `--ib-end-datetime`
- `--ib-what-to-show`
- `--ib-use-rth` or `--no-ib-use-rth`

### IBKR Preflight

To debug the broker path independently from the baseline runner:

```powershell
python scripts\test_connection.py --host 127.0.0.1 --port 4002 --client-id 21 --symbol AIR --currency EUR --exchange SMART --primary-exchange SBF
```

This preflight script:

- validates the socket/API connection
- qualifies the stock contract with primary-exchange fallback
- optionally checks streaming market data
- optionally checks historical daily bars
- prints a JSON summary that is easier to diff across runs

### Convert legacy config

To materialize an explicit baseline config from the legacy pairs file:

```powershell
python scripts\build_baseline_config.py --input config\pairs.json --output config\baseline_from_legacy_pairs.json
```

### Next recommended work

The highest-value next steps are:

1. Replace synthetic fixtures with real precomputed daily bars or research exports.
2. Feed real statistical gate metrics into the baseline instead of config stubs.
3. Unify the live IBKR loop with the local-book baseline so offline and live share the same signal path.
4. Split real candidate universes into dedicated book files per country.
