# rs-spy

Intraday Relative-Strength / Relative-Weakness (RS/RW) trading algo + backtesting
engine, implementing the spec in `algo-spec/` (start there for the *what/why*).
`IMPLEMENTATION.md` tracks the *what's actually built* — current milestone status,
deviations from spec, and known limitations. Read that before trusting any
specific number or claim below; this file only covers environment setup and how
to run things.

## Setup

Requires Python 3.11+ (built and tested on 3.14).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the runtime dependencies (pandas, numpy, duckdb, alpaca-py,
pydantic, typer, ...) plus the dev/test toolchain (pytest, pytest-cov,
hypothesis, ruff).

Real market data access (backfill scripts only — the test suite never touches
the network) needs a free Alpaca paper-trading account:

```bash
cp .env.example .env
# fill in ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY from https://alpaca.markets/
```

## Running the test suite

The entire suite is hermetic — no network calls, no real credentials needed, no
warehouse file required. It's safe to run at any time, on any machine, offline.

```bash
source .venv/bin/activate

# everything
python -m pytest -q

# with a live progress line per test (useful when debugging one area)
python -m pytest -v

# one file
python -m pytest tests/unit/test_engine_m5_backtest.py -v

# one test function
python -m pytest tests/unit/test_engine_m5_backtest.py::test_run_m5_backtest_produces_a_trade_log_and_equity_curve -v

# everything matching a name pattern (pytest's -k)
python -m pytest -k "trail_stop" -v

# stop at the first failure, drop into a debugger on error
python -m pytest -x --pdb

# coverage report (pytest-cov is already a dev dependency)
python -m pytest --cov=rs_spy --cov-report=term-missing
```

Lint (must be clean before committing):

```bash
ruff check .
```

### Test layout and categories

```
tests/unit/          hermetic, no I/O — the bulk of the suite
tests/integration/   hermetic but exercise multi-module wiring (e.g. a mocked
                      DuckDB warehouse, or the full backtest engine's data-
                      loading path) — still no real network calls
```

Within `tests/unit/`, four recurring test *shapes* are used throughout — useful
context when reading or extending tests, not separate pytest markers:

- **Golden tests**: small hand-built OHLCV fixtures with independently
  hand-computed expected values (e.g. `test_atr.py`, `test_rrs.py` — the RRS
  fixtures reproduce the worked examples from
  `documents/A-New-Measure-of-Relative-Strength.md`).
- **Property tests** (`hypothesis`): invariants checked across many generated
  inputs — e.g. ATR is never negative, RollingRRS matches an independently
  written naive reference loop. Look for `@given(...)` decorators.
- **No-lookahead / causality tests** (`test_no_lookahead.py`): for every
  indicator, truncating history to "as of bar i" and recomputing must
  reproduce exactly what the full-history run says bar i was. This is the
  single most important test class in a backtester — it's what catches an
  accidental centered window, a stray `.shift(-n)`, or a session-boundary
  leak. Any new indicator or feature function should get a case added here.
- **Spec-conformance tests**: synthetic scenarios built to trigger specific
  documented behavior (a bias bucket transition, a gate pass/fail, a state
  machine transition) and asserting the code reproduces the spec's stated
  outcome — e.g. `test_engine_m5.py`, `test_gates_m5.py`, `test_watchlist_m5.py`.

`tests/integration/test_cache_resume.py` is worth knowing about specifically:
it mocks the Alpaca client and kills a backfill mid-run (via a raised
`BaseException`, simulating a real process kill) to verify resumability — zero
duplicate API calls and no gaps on rerun.

## Manually exercising the real pipeline

These need a `.env` with real Alpaca keys (data access only — no funded/live
account required) and will create/grow `data/warehouse.duckdb` (gitignored,
currently ~3.2GB with 5 years of daily + minute bars for the 130-symbol curated
universe — see `IMPLEMENTATION.md` for exact coverage).

```bash
source .venv/bin/activate

# 1. confirm auth + inspect the real response shape (2 API calls, cheap)
python scripts/smoke_test.py

# 2. backfill daily bars (idempotent -- safe to rerun, only fetches new days)
python scripts/backfill_daily.py

# 3. backfill minute bars (idempotent, month-chunked; ~40 min for the full
#    5-year/130-symbol universe on a cold cache)
python scripts/backfill_intraday.py

# 4. run the D1 (daily-bar) walking-skeleton backtest
python scripts/run_backtest_d1.py
python scripts/run_backtest_d1.py --shorts          # include the short book

# 5. run the M5 (5-minute, intraday) event-driven backtest
python scripts/run_backtest_intraday.py
python scripts/run_backtest_intraday.py --shorts

# 6. M3.5-era D1 validation studies (gate ablation, walk-away, RRS sweep)
python scripts/run_validation_studies_m35.py
```

Backtest scripts print `08 §2`-style metrics to stdout and write a trade log +
equity curve under `reports/{d1_backtest,m5_backtest}/` (gitignored).

**Runtime note on `run_backtest_intraday.py`**: the M5 precompute layer runs
several indicators that are deliberately non-vectorized Python loops (Laguerre
RSI, trendline construction, headroom pivot search — see `algo-spec/02`'s own
stated exception for these), once per symbol across roughly 98,000 5-minute
bars per symbol over a 5-year window. A full 130-symbol run takes on the order
of tens of minutes, not seconds — this is expected, not a hang. If you only
need a quick sanity check, it's easy to point a small standalone script at
`rs_spy.backtest.engine_m5.run_m5_backtest` with a handful of symbols and a
short date slice (load bars via `rs_spy.data.loader`, then filter each
DataFrame to a date range before passing them in) instead of running the full
universe.

## UI

A Streamlit app (`app.py`) provides a browser UI over the Postgres runs-store: Runs
(auto-refreshing list), Configure & Run (launches a real M5 backtest as a detached
job), Compare, Scan & discovery (M9 nightly-scan results), and Campaigns (M10 cohort
aggregation). Needs the UI extra and a running Postgres (`docker compose up -d`):

```bash
pip install -e ".[ui]"
streamlit run app.py
```

See `IMPLEMENTATION.md`'s "M8: backtest UI" section for what's built and what's
deliberately out of scope (e.g. no real-time/live-trading view, D1 backtests or the
validation-study suite have no UI path yet).

## Verifying results by hand

- After a backfill: `duckdb data/warehouse.duckdb` then
  `select symbol, count(*) from bars where timespan='day' group by symbol order by count(*);`
  to confirm every symbol has the expected row count (a single delisted/merged
  symbol can silently truncate the whole aligned backtest calendar — see
  `IMPLEMENTATION.md`'s IPG note).
- After a backtest: open `reports/{d1_backtest,m5_backtest}/trades.csv` and
  spot-check a few entries/exits against the raw cached bars for that symbol
  and date — this is the standard sanity check used throughout this project's
  development (see `IMPLEMENTATION.md`'s per-milestone verification notes).
