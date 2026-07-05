# CLAUDE.md

Orientation for Claude/human sessions working in this repo. This is a **map**, not a
reference — it points you at the authoritative docs. Before trusting any specific number,
claim, or "what's built" detail, defer to `IMPLEMENTATION.md`.

## What this is

**rs-spy** is an intraday **Relative-Strength / Relative-Weakness (RS/RW)** day-trading algo
plus a backtesting engine. It implements the r/RealDayTrading wiki + OneOption "Market First"
methodology as an executable spec.

Core thesis (see `algo-spec/README.md` for the full version):

- **Institutions move the market, not retail.** RS/RW detects concentrated institutional
  buying/selling in a single equity — we *follow* big money, we don't predict.
- **Market First.** ~75–80% of stocks follow SPY, so the market read (bias) gates every trade:
  market up → longs only, market down → shorts only, market undecided → flat.
- **Don't trade the index itself.** SPY/QQQ are the benchmark and timing signal, not the traded
  instrument. The edge is an RS stock's proportional out/under-performance vs. the index.
- **Confirm, don't anticipate.** Enter after institutional activity is visible (RS holding,
  volume, follow-through) — a later entry for a much higher win rate.
- **Stack the checklist.** Win rate rises monotonically with conditions met; encoded as a
  weighted score with hard gates for the non-negotiable rules.

## The four source-of-truth docs (and when to read each)

| Doc | Role | Read it when |
|-----|------|--------------|
| `algo-spec/` | The **what/why** — the formal spec (README + 8 numbered docs). | You need the intended behavior of an engine/indicator. |
| `documents/` | The **origin** — raw r/RealDayTrading wiki posts the spec formalizes (conceptual/educational, e.g. *A Simple Strategy*, *Keeping it Really Simple*). | You want the intuition/rationale behind a rule. |
| `IMPLEMENTATION.md` | The **what's-actually-built** — milestone status, every deliberate deviation from spec, and a numbered **known-limitations** list (~27 items). ~1500 lines, authoritative. | **Always, before trusting a number or claiming something works.** |
| `SESSION_SUMMARY.md` | A short handoff pointer, refreshed at session boundaries. | Starting a fresh session — orient here, then go to `IMPLEMENTATION.md`. |

`algo-spec/` index: `01` data requirements · `02` indicators & formulas (RRS, ATR, VWAP, RVOL,
Heikin-Ashi, Laguerre RSI, SMA stack, trendlines) · `03` market-bias engine · `04` stock-selection
engine · `05` long algo · `06` short algo · `07` risk management · `08` backtesting & validation.

## Codebase map (`src/rs_spy/`)

**Two-cadence design runs throughout**: a **D1 "walking skeleton"** (built first, M0–M3.5) and the
real **M5 intraday** system (M4–M7.5). Modules/functions with `_d1` vs `_m5` suffixes (or paired
names) reflect this — the M5 variants are the live system; D1 is the simplified precursor.

```
src/rs_spy/
├── config.py            Pydantic Settings: paths, Alpaca creds, warehouse path, database_url
├── universe.py          Load config/universe.yaml + reference_overrides.yaml (symbols, sectors, benchmarks, earnings blackout)
│
├── data/                Data ingestion + the DuckDB warehouse
│   ├── alpaca_client.py   Thin alpaca-py wrapper (vendor isolation, IEX feed)
│   ├── rate_limiter.py    Sliding-window limiter (Alpaca free tier)
│   ├── manifest.py        Resumable-backfill bookkeeping (fetch_manifest table)
│   ├── ingest.py          Backfill orchestration: plan → fetch → write → mark done
│   ├── warehouse.py       DuckDB connection + schema (bars + fetch_manifest)
│   ├── loader.py          Read cached bars → per-symbol DataFrames (daily/M1/M5)
│   ├── resample.py        1-min → true M5/M15/M30 (causal aggregation)
│   └── session.py         RTH session filter (strips pre/post-market bars)
│
├── indicators/          All formulas (algo-spec 02) — rrs, atr, vwap, rvol, heikin_ashi,
│                         laguerre_rsi, sma_stack, headroom, trendlines, candle_structure
│
├── bias/                Market Bias Engine (algo-spec 03, the "Market First" gate)
│   ├── buckets.py         Bias-bucket vocabulary + score→bucket hysteresis
│   ├── regime.py          D1 regime classifier (TREND_UP / CHOP / TREND_DOWN)
│   ├── daily_context.py   Pre-open daily-context pass
│   ├── trigger.py         Shared trendline-breach timing trigger
│   ├── engine.py          Full M5 bias engine (8 components, EMA smoothing)
│   └── engine_d1.py       D1 walking-skeleton bias engine (simplified)
│
├── scan/                Nightly universe scan / discovery (algo-spec 01 §4, M9)
│   ├── config.py          ScanConfig: iex/sip threshold presets, listing-heuristic allow/deny lists
│   ├── engine.py          As-of metrics (causal SQL) + gate application + ScanCoverageError refusal
│   ├── bars.py            Separate scan DuckDB warehouse + self-healing daily-bar refresh
│   ├── onboarding.py      Most-active auto-onboarding: candidate selection + dual daily/minute backfill
│   └── nightly.py         Orchestrator: screener capture -> refresh+scan -> record -> onboard -> re-run
│
├── selection/           Stock Selection Engine / RS-RW scanner (algo-spec 04)
│   ├── features.py / features_m5.py   Per-symbol D1 / M5 feature composition
│   ├── gates.py           Hard gates (§2)
│   ├── scoring.py         Composite weighted score
│   └── watchlist.py       Per-symbol state machine + daily tradeable-list build
│
├── algo/                Trade engines (algo-spec 05/06/07)
│   ├── long.py            Long entry qualification + exit signals
│   ├── short.py           Short mirror (stricter gates, 0.75× size)
│   └── risk.py            Position sizing, ATR stops, loss limits, kill switches
│
├── backtest/            Backtest engines + validation studies
│   ├── engine.py          D1 walking-skeleton backtest (two-phase)
│   ├── engine_m5.py       ★ M5-cadence event-driven backtest — the primary engine
│   ├── broker_sim.py      Order-fill simulation (marketable-limit, next-bar fills, slippage)
│   ├── metrics.py         Primary metrics (win rate, profit factor, … — algo-spec 08 §2)
│   └── studies/           Validation studies (ablation, walk-away, rrs-sensitivity,
│                          bias-confusion, time-of-day, gate-audit, trigger-skill) — *_m5 variants are live
│
├── store/               Postgres runs-store: runs/trades/equity/status for concurrent backtests + UI
│   └── (connection, schema, repository, serialize)
├── jobs/                Detached backtest job runner (runner + launch) — writes results to Postgres
│
└── reporting/           Placeholder (empty)
```

## How to run things

Setup (see `README.md` for the full version):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # Alpaca paper creds — needed for backfill only
```

There are **no console_scripts** — everything is a standalone Typer script:

| Command | Purpose |
|---------|---------|
| `python scripts/run_backtest_intraday.py [--shorts]` | **Primary M5 backtest** → `reports/m5_backtest/` |
| `python scripts/run_backtest_d1.py [--shorts]` | D1 walking-skeleton backtest |
| `python scripts/backfill_daily.py` / `backfill_intraday.py` | Populate the DuckDB warehouse from Alpaca (needs `.env`) |
| `python scripts/run_validation_studies.py` | Full M5 validation study suite (algo-spec 08 §3) |
| `python scripts/run_tuning_sweep.py --window 18` | Tuning-campaign parameter sweep |
| `python scripts/run_backtest_job.py --run-id <uuid>` | DB-native single run → Postgres (used by the UI/job runner) |
| `python scripts/run_nightly_scan.py [--as-of DATE] [--no-onboard]` | Nightly universe scan + screener capture + most-active onboarding |

## Data & storage

- **Market data**: a single **DuckDB** file, `data/warehouse.duckdb` (~3.4GB, gitignored). Two tables:
  `bars` (`symbol, timespan, ts, ohlcv, vwap, trade_count`) and `fetch_manifest` (resumability).
  `ts` is true UTC (see the tz-bug comment in `data/warehouse.py`). Read via `data/loader.py`, written
  via `data/ingest.py`. **Backtests open it read-only** (`connect(path, read_only=True)`) so multiple
  runs can read concurrently; ingestion opens it read-write.
- **Scan data**: a separate **DuckDB** file, `data/scan.duckdb` (~1GB, gitignored,
  `Settings.resolved_scan_warehouse_path`) — same `bars`/`fetch_manifest` schema as the main
  warehouse, but holds the broad ~14k-symbol daily-bar universe the nightly scan (M9) screens,
  kept isolated so it never bleeds into curated-universe queries or contends with concurrent
  backtest reads on `warehouse.duckdb`.
- **Backtest results**: legacy CSV/JSON under `reports/<...>/`, plus the **Postgres runs-store** (Docker,
  `docker compose up -d`) holding `runs`/`trades`/`equity_curves` with status — the queryable home for
  concurrent runs and the future UI. Connection via `Settings.database_url`. Also holds 4 M9
  scan tables — `scan_runs`/`universe_snapshots`/`screener_snapshots`/`onboarded_symbols` — via
  `store/scan_repository.py`.
- **Config**: `config/{universe.yaml, reference_overrides.yaml, backtest_default.yaml}`, loaded by
  `universe.py`. `.env` holds Alpaca creds + `database_url` overrides.

## Dev workflow

- **Tests are hermetic** — no network, no creds, no warehouse file required. Run `python -m pytest -q`
  (251 tests). Postgres integration tests (marked `integration`) spin an ephemeral container via
  testcontainers and **auto-skip when Docker isn't available**; run only them with `pytest -m integration`,
  skip them with `pytest -m "not integration"`. (The conftest disables testcontainers' Ryuk reaper, which
  hangs on local Docker Desktop.)
- **Lint must be clean**: `ruff check .` (line-length 100).
- The project uses **Subagent-Driven Development**: a fresh implementer subagent per task (TDD), a fresh
  reviewer per task (spec compliance + code quality, with bug-injection verification), and a final
  whole-branch review each milestone. Plans live in `docs/superpowers/plans/`.
- Strong **"document, don't silently approximate"** norm: every deliberate simplification or scope cut is
  written down (module docstrings and/or `IMPLEMENTATION.md`'s known-limitations list), never left implicit.
- **Trust but verify**: subagent/reviewer claims get independently spot-checked before acceptance.

## The tunable surface (`BacktestConfigM5`)

`src/rs_spy/backtest/engine_m5.py:45` — the ~28-field dataclass that parameterizes an M5 backtest
(risk sizing, concurrency caps, score floors, RRS windows/thresholds, disabled-gate toggles, shorts
on/off, stop ATR multiplier, dip-hold mode, …). Promoted defaults vs. the spec are commented inline
(e.g. `rrs_m5_window=18`, `bias_hold_bars=1`).

**Critical distinction** (docstring at `engine_m5.py:350-364`): config fields are either **prepare-baked**
(changing them requires a fresh ~15–20 min `_prepare_m5` precompute — e.g. `rrs_m5_window`,
`min_adv_shares`, the RRS thresholds) or **event-loop-only** (free to vary against a shared `PreparedM5`
— e.g. risk sizing, concurrency, `stop_atr_mult`, `bias_hold_bars`, `dip_hold_mode`). This is the key
lever for fast parameter sweeps: build one `PreparedM5`, then vary event-loop-only knobs cheaply.
