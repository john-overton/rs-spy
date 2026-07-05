# Spec: Universe 500 + backtest campaign (M10)

**Status**: approved design (brainstormed 2026-07-05). Input to `writing-plans`.
**Depends on**: M9 (nightly universe scan: `scan/` package, scan warehouse `data/scan.duckdb`,
Postgres scan tables), the Postgres runs-store + detached job runner, and the
`BacktestConfigM5.extra_symbols` precedent (inert-in-engine, runner-consumed config fields).

## Purpose

Expand the backtest universe from the curated 130 to **~500 symbols** and run a backtest
campaign at ~4× sample size. Every M7.5 finding is flagged "directional, tiny sample"
(13 trades on the promoted config); universe expansion is the ledger's stated remaining
sample-size multiplier. This milestone makes the expansion reproducible (no hand-maintained
lists) and produces the campaign results that confirm or kill the M7.5 conclusions.

## Decisions (brainstorming outcomes)

- **Selection**: curated 130 kept (continuity with all prior ledger rows) + top-up to 500
  from the latest M9 scan's passing set, ranked by `adv_dollars`, requiring a **first daily
  bar ≤ 2021-07-05** in the scan warehouse (continuous 5-year history, mechanically enforced —
  the same rule the 130 were hand-picked under). Benchmarks excluded from the trade list.
- **Sectors**: one-shot **yfinance enrichment** at build time → committed
  `config/sectors_500.yaml`; unresolved symbols fall back to `UNKNOWN`. Runtime code never
  imports yfinance (dev-only dependency). Needed because the engine's tradeable-list builder
  caps `max_per_sector=2` and Alpaca provides no sector field.
- **Scale strategy**: **cohort runs** — split the 500 into 4 cohorts of ~125, each its own
  detached job (the machine is 24 GB and the M7.5 sweep already OOM'd at 130 symbols;
  4× data in one process is not credible without engine surgery). Documented caveat:
  portfolio-level constraints (max-concurrent, daily loss limits, lockouts) apply per cohort,
  not across all 500 — right for signal-quality/sample-size questions, not a literal
  portfolio simulation.
- **Campaign content** (all four selected): (1) promoted-baseline aggregate (w18,
  `bias_hold_bars=1`) — does PF ~3.7 hold?; (2) key tuning-cell re-check — RRS window
  {12, 18, 24} × bias_hold {1, 2}; (3) full validation study suite on the expanded universe;
  (4) shorts re-assessment across the 500.

## Architecture

All list-building is **build-time, committed-artifact** work; all campaign running reuses the
existing detached-jobs + Postgres runs-store machinery.

### Components

- **`scripts/enrich_sectors.py`** (one-shot, dev-only): given a symbol list, pull
  `sector` per symbol from yfinance → `config/sectors_500.yaml` with a header documenting
  source + date. Unresolved → omitted (consumers default to UNKNOWN). Never imported at
  runtime; `yfinance` goes in a `universe` extras group in `pyproject.toml`.
- **`scripts/build_universe_500.py`**: generates `config/universe_500.yaml` (same schema as
  `universe.yaml`) from: curated 130 (verbatim, with their existing sectors) + scan top-up.
  Top-up query runs against `data/scan.duckdb` + the latest `universe_snapshots` scan date in
  Postgres: passing symbols, not already curated, first daily bar ≤ the history cutoff,
  ranked by `adv_dollars` descending, take until 500 total. Sector labels merged from
  `config/sectors_500.yaml`. Output is committed; regeneration is reviewable via git diff.
- **Pure selection logic** lives in `src/rs_spy/scan/universe500.py` (testable without
  network/Postgres): `select_topup(passing_metrics, curated, *, history_cutoff, target=500)`
  and `build_universe_yaml(curated_yaml, topup, sectors)` — the scripts are thin shells.
- **`BacktestConfigM5` additions** (both inert in the engine, consumed by `jobs/runner.py`,
  exactly the `extra_symbols` precedent):
  - `universe_file: str = "universe.yaml"` — which universe YAML the runner loads
    (benchmarks, sectors, earnings blackout come from this file).
  - `trade_symbols_override: tuple = ()` — when non-empty, **replaces** the universe's trade
    list (must be a subset of the loaded universe's symbols; the runner validates and fails
    the run loudly otherwise). This is how a cohort run selects its ~125 symbols.
  Serialize round-trip (tuple↔list) mirrors `extra_symbols`.
- **`src/rs_spy/backtest/campaign.py`**: `split_cohorts(symbols, n_cohorts=4, seed=…)` —
  deterministic, sector-stratified split (so each cohort keeps sector diversity under the
  per-sector cap); `launch_campaign(conn, universe_file, cohorts, config_variants, tag)` —
  one Postgres run per (cohort × variant), labels `m10-<tag>-<variant>-c<n>`, launched via
  `jobs/launch.launch_run`. Sequential-by-default launch with a `--max-parallel` knob
  (default 2: two ~15-20 min prepares fit in 24 GB; four might not).
- **`src/rs_spy/backtest/aggregate.py`**: `aggregate_campaign(conn, tag, variant)` — pools
  trades + equity across the variant's cohort runs from Postgres, recomputes the standard
  metrics table (same functions as `backtest/metrics.py`), **refuses** (raises) if any cohort
  run is missing/failed/still-running rather than silently pooling partial results.
  `scripts/aggregate_campaign.py` prints the table and writes
  `reports/m10_campaign/<tag>-<variant>.csv`.
- **Campaign driver `scripts/run_campaign_500.py`**: creates + launches the selected variants
  (baseline; w12/w18/w24 × hold1/hold2; shorts-on) over the 4 cohorts. The study suite runs
  through the existing `run_validation_studies.py` per cohort with `--config-json` overrides
  where needed (details in the plan).

### Earnings blackout at 500

`reference_overrides.yaml` carries hand-maintained earnings dates for the 130 only. The 370
new symbols get **no earnings blackout** — `earnings_blackout.get(sym)` already tolerates
missing symbols (verified in M9). Disclosed as a known limitation in IMPLEMENTATION.md:
earnings-day trades in the top-up set are possible in campaign results (the spec's blackout
rule is silently un-enforced for them; acceptable for signal-quality measurement, must be
fixed before live trading on the expanded universe).

## Data flow & operations

1. Backfill: `backfill_intraday.py` / `backfill_daily.py` pointed at the 370 new symbols
   (manifest-resumable; ~12-30 h of rate-limited API time over nights; warehouse grows
   ~3.1 GB → ~12 GB — disk is fine). **Pause the 16:00 nightly cron during the initial bulk
   run** (both write the main warehouse; collisions are benign but noisy — the onboarding
   stage is isolated and retries — pausing is the clean option, documented in the plan).
2. Build: enrich sectors → build universe_500.yaml → commit both.
3. Campaign: launch cohort runs (max 2 parallel) → aggregate per variant → ledger rows +
   IMPLEMENTATION.md M10 section, same discipline as M7.5.

## Error handling

- Backfill interruptions resume via the manifest (existing behavior).
- Cohort jobs are independent processes: one OOM/crash fails one run row in Postgres; the
  campaign driver can relaunch just that run.
- The aggregator hard-fails on incomplete campaigns (no silent partial pooling).
- The universe builder hard-fails if the scan warehouse or scan snapshot is missing/stale
  (>7 days old) rather than building from stale liquidity ranks.
- yfinance enrichment failures leave symbols out of the YAML → UNKNOWN at runtime (visible,
  counted by the builder's summary output).

## Testing

Hermetic unit tests for: `select_topup` (ranking, cutoff, curated-precedence, target size),
`build_universe_yaml` (schema, sector merge, UNKNOWN fallback), `split_cohorts` (determinism,
stratification, benchmarks-in-every-cohort), config field round-trips + runner consumption
(`universe_file` load, `trade_symbols_override` subset validation), aggregator (pooling,
refusal on missing/failed cohorts). yfinance is faked in tests. Real-data steps (backfill,
campaign runs, calibration of actual counts) are execution tasks like M9's Task 9.

## Out of scope

- Engine memory work for single-process 500 (revisit only if portfolio-realism questions
  demand it; cohort semantics documented instead).
- Earnings data for the 370 (disclosed limitation).
- Any change to the curated 130, `universe.yaml`, or default backtest behavior — a default
  run must reproduce bit-for-bit (same global constraint as M9).
- Live/paper trading (that's discovery milestone #3), and the real-time signal engine
  (milestone C of this brainstorm, own spec later).
