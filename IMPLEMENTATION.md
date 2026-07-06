# Implementation Status

Status snapshot for resuming work. Specs live in `algo-spec/` (the *what/why*);
this document is the *what's actually built* (the *how*, and where it
deviates from spec). Written at the M4 checkpoint, updated at the M5, M6, and
M7 checkpoints. Read "Critical: a real timezone bug affected all data before
this point" before trusting any date in an earlier report (trade dates,
trigger dates, etc.) — the underlying OHLCV values and all numeric results
were never wrong, only date labels.

## Milestone tracker

- M0-M3: D1 walking skeleton (universe, ingestion, D1 indicators, D1
  bias/selection engines, D1 backtest) — **complete**.
- M3.5: validation studies (ablation, walk-away, RRS sensitivity), universe
  expansion 28 -> 130 — **complete**.
- M4: timezone bug fix + full warehouse rebuild, 5-year/130-symbol minute
  backfill, M5-only indicators (VWAP, RVOL, Laguerre RSI) — **complete**.
- **M5: full intraday market-bias engine (algo-spec 03) + stock-selection
  engine (algo-spec 04) at true M5 cadence — complete (this checkpoint).**
  Engine functions only, unit tested; no backtest replay loop, no order
  execution, no position management (that's M6). See "M5:..." section below.
- **M6: M5-cadence event-driven backtest engine, long/short algo per
  algo-spec 05/06/07 — complete (this checkpoint).** See "M6:..." section
  below — the engine and algo code are built, unit tested (182 tests), and
  reviewed. Two real crashes found on real data are fixed; the real backtest
  now runs end-to-end and produces a real (if thin) result: 0 trades over
  1252 trading days / 128 symbols, root-caused (not just reported) via
  direct gate-pass-rate/watchlist-state inspection.
- **M7: full validation study suite (08 §3), M5 cadence — complete (this
  checkpoint).** Pre-work fixed the ADV-gate cadence bug found during M6
  (0 → 3 real trades) and built a committed full-universe gate-pass-rate/
  watchlist-state audit tool. Built and ran the full 5-study suite
  (ablation, walk-away, RRS sensitivity, bias confusion matrix, time-of-day/
  regime slicing) against the real 3-trade sample, per the user's explicit
  direction to proceed rather than first expand the universe or loosen
  gates. The final whole-branch review found and fixed one real bug (the
  ablation study's `disable_bias` lever was a silent no-op in the M5
  engine); the ablation study was re-run against real data after the fix.
  Real, actionable finding: the RRS sensitivity sweep found
  `rrs_m5_window=18` produces 10 trades (vs. 3 at the spec default of 12) —
  see "M7: full validation study suite" section
  below for the complete results and interpretation. Test suite: 204 tests.
- M7.5: tuning-campaign enablers + two experiment rounds — `rrs_m5_window`
  promoted 12 -> 18, `bias_hold_bars=1` promoted after a robustness pass, the
  full validation-study suite re-run on the promoted baseline (gate-ablation
  monotonicity finally confirmed) — **complete**. See the M7.5 sections below.
- **M8: backtest UI — complete.** A Streamlit app (`app.py` + `src/rs_spy/ui/`) over the
  Postgres runs-store built in M6/M7.5: 5 pages (Runs, Configure & Run, Compare, Scan &
  discovery, Campaigns), an out-of-process job-launch model (a run is a detached subprocess
  polled via Postgres, never in-thread), and `st.fragment(run_every="5s")` auto-refresh —
  329 unit tests (`AppTest` + monkeypatched data layer). A different axis of work than
  M4-M7.5/M9 (presentation over an existing store, not new trading-system behavior); numbered
  after M7.5 chronologically but built independently of, and merged before, M9's Task 9
  calibration. See "M8: backtest UI" section below — includes a deliberately deferred
  run-launch smoke (a concurrent bulk backfill held the warehouse's write lock).
- **M9: nightly universe scan (discovery half of algo-spec 01 §4) — complete
  except Step 4 (first live nightly run with onboarding), which needs a real
  trading session and is pending the next session (Mon 2026-07-06).** Built a
  self-contained `scan/` package (config/engine/bars/onboarding/nightly) that
  answers "what should be in the tradeable universe tonight" from broad
  Alpaca daily bars, one code path for both the live nightly scan and
  point-in-time reconstruction. Real-data calibration (Task 9, as-of
  2026-07-02, 14,021 assets): IEX thresholds promoted to 40k shares/$2M ADV
  (1,450 passing, 128/128 curated symbols), measured universe-coverage
  fraction 0.993 against the 0.80 refusal floor, and PIT spot-checks
  quantifying survivorship decay (~1y back still solid, ~2y+ back refuses on
  coverage). See "M9: nightly universe scan (discovery)" section below.
- **M10: universe-500 expansion + backtest campaign — complete.** Built a 500-symbol
  universe (128 curated + 372 scan-ranked top-up, Nasdaq-screener sector enrichment after
  yfinance was hard-blocked by Yahoo) and a 4-cohort sector-stratified campaign runner, then
  ran a 5-variant campaign (20 cohort runs) re-testing the M7.5 promoted config out of the
  curated universe. Headline, sobering result: the `rrs_m5_window=18` promotion **inverts**
  at 500 symbols (PF 0.86, the sweep's worst) while the spec default `window=12` wins (PF
  1.91) — curated-universe overfitting, vindicating the tiny-sample caveat every M7.5 result
  carried. `bias_hold_bars=1` survives (hold=2 is decisively worse, PF 0.53). Shorts remain
  weak (PF 0.77). See "M10: universe 500 + backtest campaign" section below.

## TL;DR

A working, real-data-verified, end-to-end **daily-bar (D1)** backtest of the
RS/RW system runs cleanly against 5+ years of cached Alpaca daily bars for a
**130-symbol** curated universe (grown from 28 during M3.5). M3.5 found real
signal (437 walk-away entry signals, stable ~1.9R mean MFE; an RRS-window
sensitivity sweep favoring `window=3` over the M3 default of 5) but also
revealed that the D1 approximation trades on multi-day/multi-week holds
(5-35 days) — a fundamentally different shape from the actual intraday spec,
so tuning it further wasn't worth continued investment. M4 backfilled 5
years of minute bars for the full 130-symbol universe (44.1M rows) and, in
the process, found and fixed two real bugs: a timezone bug that silently
corrupted every stored bar's date/time (see below — serious, now fixed,
warehouse fully rebuilt), and an RVOL design flaw (arrival-order indexing
breaks on IEX's frequent data gaps for less-liquid names). Also built ahead
of schedule: the three M5-only indicators with no D1 equivalent (VWAP,
RVOL, Laguerre RSI). **M5 (this checkpoint) built the real thing on top of
all of that**: `data/resample.py` (1-min -> true 5-min -> H1 aggregation +
causal cross-timeframe alignment), the full 8-component M5 market-bias
engine (`bias/engine.py`), per-stock M5 feature computation
(`selection/features_m5.py`), and the full 9-gate/7-weight M5 selection
engine (`selection/gates.py`/`scoring.py` extensions, `selection/watchlist.py`
LRSI dip-arm + trigger bypass). Found and fixed five real bugs during the
build (see "M5:..." section below) — most notably a genuine 1-minute
lookahead leak in the M1-to-M5 alignment path. **124 tests passing, lint
clean** at the M5 checkpoint. **M6 built the actual M5-cadence backtest
engine and long/short algo** (risk sizing, order-fill simulation,
entry/exit logic, the bar-by-bar event loop — 182 tests total) and, after
fixing two more real bugs that only real market data exposed (a `pd.NA`
dtype-upcast crash in shared candle-structure code, and a QQQ/SPY M5
index-misalignment crash in the bias engine), ran the real thing end-to-end
against the full 128-symbol/5-year warehouse: **0 trades over 1252 trading
days.** Root-caused, not just reported — direct inspection (the same method
M3.5 used at D1) found bias and the trigger both behave plausibly (BULL
38.86% of bars, `LONG_TRIGGER` fires 1,591 times in 5 years), but the joint
pass rate across the full 9-gate M5 selection stack is 0.00-0.02% of bars
even for large, liquid names — a more severe version of the same
gate-confluence rarity M3.5 found and fixed by expanding the D1 universe,
now the lead item for M7. See "M6:..." below for the full diagnosis.

## Critical: a real timezone bug affected all data before this point

Found while sanity-checking backfilled data during M4: `data/warehouse.py`'s
DuckDB schema declares `ts` as a plain `TIMESTAMP` (not `TIMESTAMPTZ`), and
DuckDB's session `TimeZone` setting defaults to the OS timezone. When a
tz-aware UTC pandas column is inserted into a naive `TIMESTAMP` column,
DuckDB silently converts it to *local session time* first, then strips the
tz label — so every stored timestamp was shifted by the OS's UTC offset
(this machine: `America/Chicago`, i.e. -5/-6h) without any error or warning.
For **daily** bars (each timestamped at midnight ET), that -5/-6h shift
crosses midnight and lands on the *previous calendar date* every single
time (confirmed: a backtest trade was dated on a Sunday, which is
impossible for real trading data — the actual session was the following
Monday). For **minute** bars, every timestamp was off by 5-6 hours,
corrupting session/RTH boundaries.

**Fixed** in `warehouse.py::connect()` with `con.execute("SET TimeZone='UTC'")`
right after connecting, making the conversion a no-op. **Both the daily and
minute warehouses were wiped and fully rebuilt** after the fix (verified:
daily bar dates are now all weekdays, minute bars for AAPL on 2024-03-01 now
correctly span 14:30-20:59 UTC = 09:30-15:59 ET, exactly 390 RTH bars).

**Impact assessment**: the shift was a *constant* offset applied uniformly
to every symbol (including SPY/QQQ), so all relative time-series math
(rolling windows, ATR, RRS, day-over-day changes, bias/gate/score logic,
P&L) is shift-invariant and was **never numerically wrong** — re-running
the D1 backtest post-fix reproduced the exact same 4 trades, win rate,
and PnL as before, just with correct calendar dates. Only human-facing date
*labels* (trade entry/exit dates, trigger dates) in earlier reports were
wrong by one day and should not be cross-referenced against real calendar
events. `reports/d1_backtest/trades.csv` has been regenerated with correct
dates.

## Environment / how to run things

```bash
cd /Users/johnoverton/Development/rs-spy
source .venv/bin/activate        # venv already created, deps installed

python -m pytest -q              # 124 tests, hermetic, no network/credentials needed
ruff check .                     # clean

python scripts/smoke_test.py               # needs .env (Alpaca keys) -- confirms auth + data shape
python scripts/backfill_daily.py           # idempotent; re-run any time, only fetches new days
python scripts/backfill_intraday.py        # idempotent; minute bars, month-chunked, ~40min full run
python scripts/run_backtest_d1.py          # runs the D1 backtest, writes reports/d1_backtest/trades.csv
python scripts/run_validation_studies_m35.py  # M3.5: ablation + walk-away + RRS sweep, writes reports/m35_studies/
```

`.env` has real Alpaca paper-trading keys (gitignored). `data/warehouse.duckdb`
has the cached bars (gitignored, ~3.2GB: 162.7k daily rows + 44.1M minute
rows). `reports/` has the last backtest's trade log and equity curve
(gitignored).

## Stack (as built, not just planned)

Python 3.14, venv at `.venv/`. Key deps: `alpaca-py` 0.43.5, `pandas`,
`numpy`, `duckdb`, `pyarrow`, `pydantic`/`pydantic-settings`, `pyyaml`,
`typer`, `pytest`+`hypothesis`, `ruff`.

## Repo layout (actual)

```
config/
  universe.yaml              # SPY, QQQ + 128 curated cross-sector symbols (grown from 28 during M3.5)
  reference_overrides.yaml   # per-symbol earnings_blackout dict -- all empty lists currently (stub)
  backtest_default.yaml      # thresholds from algo-spec 02-08, as config (not all wired up yet)

src/rs_spy/
  config.py                  # pydantic-settings, reads .env
  universe.py                 # Universe/BenchmarkSpec/SymbolSpec models, load_universe(), load_earnings_blackout()

  data/
    alpaca_client.py         # AlpacaClient.fetch_bars(symbols, timespan, start, end) -> DataFrame
                              #   IEX feed, adjustment="all", sliding-window rate limit + 429 backoff
    rate_limiter.py           # SlidingWindowLimiter (deque-based; 200 calls/min -- Alpaca's REAL free-tier
                               #   limit, confirmed; the plan's original "10k calls/min" was wrong)
    warehouse.py               # DuckDB connect() + schema (bars, fetch_manifest); forces SET TimeZone='UTC'
                               #   (see "Critical: a real timezone bug" above)
    manifest.py                 # pending_symbols()/record() -- "already fetched?" bookkeeping
    ingest.py                    # backfill(): year OR month chunks, optional symbol_batch_size, resumable
    loader.py                     # load_daily_bars(), load_minute_bars(rth_only=True), + universe-wide variants
    session.py                     # rth_mask()/filter_rth() -- ET-timezone-aware RTH filter (M4, see below)
    schemas.py                     # AggBar, FetchTask pydantic models (lightly used so far)
    resample.py                     # (M5, new) resample_ohlcv(freq, closed=) -- 1min->M5->H1 aggregation;
                                     #   align_causal()/align_daily_to_intraday() -- causal cross-timeframe alignment

  indicators/                # D1-capable set unchanged; M5-only set (vwap, rvol, laguerre_rsi) now built (M4)
    atr.py                    # Wilder ATR, vectorized (seeded EWM splice)
    rrs.py                     # RRS / PowerIndex / ExpectedPC / RollingRRS -- generic window param, used at D1 (L=5)
    heikin_ashi.py               # HA transform + signed continuation streak (ha_cont_d1)
    sma_stack.py                   # ABOVE_ALL/BELOW_ALL/MIXED vs 50/100/200 SMA
    headroom.py                     # pivot_highs/pivot_lows (centered, confirmation-lag design) + headroom_long/short
    trendlines.py                    # down/up_trendline (loop over confirmed pivots) + breach_up/breach_down
    candle_structure.py                # stacked_count, overlap_ratio, chop_ratio, volume_ratio_d1, follow_through
    vwap.py                              # session VWAP, resets each day -- requires RTH-filtered input (M4, new)
    rvol.py                               # time-of-day RVOL keyed by real ET clock time, not arrival order (M4, new)
    laguerre_rsi.py                        # Ehlers 4-stage cascade, non-vectorized loop per plan's exception (M4, new)

  bias/
    regime.py                  # regime_d1(): TREND_UP/CHOP/TREND_DOWN via linreg slope + SMA50 slope agreement
    engine_d1.py                # bias_series_d1(): 8-component D1 score -> EMA(3) smooth -> hysteresis bucket
                                 #   + compute_trigger(): LONG_TRIGGER/SHORT_TRIGGER (D1 version of 03 §5)
                                 #   (M5, refactored) now a thin wrapper over buckets.py/trigger.py, below
    buckets.py                   # (M5, new) hysteresis bucket vocabulary + apply_hysteresis() -- extracted
                                  #   from engine_d1.py, shared by both D1 and M5 engines
    trigger.py                    # (M5, new) compute_trendline_trigger() -- extracted from engine_d1.py, shared
    daily_context.py                # (M5, new) daily_context_series(): pre-open pass -- regime_d1, prior-day
                                     #   D1 high/low/close, suspect_rally/selloff breakout audit (03 §2);
                                     #   un-shifted by design, callers align with shift=1
    engine.py                        # (M5, new) full 8-component M5 bias engine (03 §3-6): compute_raw_score(),
                                      #   bias_series() -- EMA-3 smoothing, hysteresis, trigger, warmup, flip_flatten

  selection/
    features.py                 # compute_symbol_features(): per-symbol D1 feature DataFrame (rrs, ha_cont, etc.)
    gates.py                      # D1-available hard gates (G1,G4,G5,G6,G7,G8; G2/G3/G9 dropped, no D1 equivalent)
                                   # (M5, extended) full G1-G9 set added at bottom: gate_vwap_*, gate_rrs_m5_*,
                                   #   gate_not_one_candle_wonder, gate_no_gap_exclusion, gate_benchmark_crosscheck_*,
                                   #   gates_pass_long_m5/gates_pass_short_m5
    scoring.py                     # score_long/score_short, W1->W2 weight redistribution (see below)
                                    # (M5, extended) score_long_m5/score_short_m5 -- full un-redistributed 7-weight
                                    #   table (04 §4), now that RollingRRS_M5 (W1) is available
    features_m5.py                  # (M5, new) compute_symbol_features_m5(): per-stock M5 feature computation --
                                     #   RRS_M5 (H1 ATR), VWAP/RVOL (1-min bars aligned to M5), LRSI, one-candle-
                                     #   wonder anti-pattern, gap_pct, D1-feature passthrough
    watchlist.py                    # IDLE/QUALIFIED/DIP_ARMED/ENTRY_EVAL state machine, build_tradeable_list()
                                     # (M5, extended) next_state_long/_short gained keyword-only lrsi_prev/lrsi_now
                                     #   (04 §6 dip-arm OR-condition); new apply_trigger_bypass() (trigger-day
                                     #   direct-entry exception)

  backtest/
    engine.py                    # run_d1_backtest(): day-loop, close(t)-signal -> open(t+1)-fill, full position mgmt
                                  #   BacktestConfig now also carries M3.5 study knobs (disabled_gates, rrs_window,
                                  #   rrs_use_rolling, rrs_threshold_long/short) -- all default to the M3 baseline
    metrics.py                    # compute_metrics() (08 §2), metrics_by_direction()
    studies/                       # M3.5, now built
      ablation.py                   # run_gate_ablation(): 08 §3.1, disable each of {bias,rrs,ha,sma} one at a time
      walk_away.py                   # run_walk_away(): 08 §3.2, MFE/MAE per entry signal vs. realized trade R
      rrs_sensitivity.py               # run_rrs_sensitivity(): 08 §3.3, window x threshold x rolling/raw grid
  algo/, reporting/                # empty packages, M6/M7 work goes here

scripts/
  smoke_test.py, backfill_daily.py, backfill_intraday.py, run_backtest_d1.py, run_validation_studies_m35.py
  # run_backtest_intraday.py, run_validation_studies.py (full 08 suite) NOT built yet (M6/M7)

tests/unit/       # 23 files (M5, new: test_resample.py, test_daily_context.py, test_features_m5.py,
                  #   test_engine_m5.py, test_gates_m5.py, test_scoring_m5.py, test_watchlist_m5.py), golden +
                  #   property(hypothesis) + no-lookahead causality + M3.5 study smoke tests, all hermetic
tests/integration/ # test_cache_resume.py -- kill/resume + error-retry semantics + symbol-batch semantics, mocked HTTP, no network
```

## Data currently cached

- **Universe**: SPY, QQQ (benchmarks) + 128 curated large/mid-cap symbols across 11 GICS sectors
  (see `config/universe.yaml`; grown from 28 during M3.5 -- see "Universe expansion" below). Not
  the full 800-1500 symbol scan from spec `01` -- still a deliberate phase-1 scoping decision,
  unrelated to any API limit (Alpaca free tier has no meaningful rate limit for this scale).
- **Daily coverage**: 2021-07-05 through 2026-06-30 (~5 years, 1251 bars/symbol for all 130
  symbols, ~162.7k total rows), `adjustment="all"` (splits+dividends), IEX feed. One symbol (IPG)
  was fetched then dropped from the universe after being found delisted mid-window (see below);
  its rows remain cached but unused.
- **Minute coverage (M4, new)**: same 5-year window and universe, ~44.1M rows. Includes
  pre/post-market bars (Alpaca's minute feed is not RTH-only -- filter via
  `data.loader.load_minute_bars(rth_only=True)`, the default). Coverage density varies hugely by
  symbol -- SPY has ~494k rows over the window (near-complete, a bar almost every RTH minute);
  BKNG has only ~105k (IEX only reports a bar when a trade actually prints there that minute, and
  BKNG is comparatively thin on that single venue). This is expected for an IEX-only feed, not a
  data quality bug, but it's exactly why `indicators/rvol.py` keys its time-of-day baseline by
  real ET clock time rather than arrival order (see deviation list below).

## Key deviations from the original spec (all deliberate, all documented in code)

These matter for anyone reading backtest results without also reading the code:

1. **`bias/engine_d1.py` is NOT a faithful daily subset of `03-market-bias-engine.md`.**
   The real spec's 8 intraday score components (VWAP side, M5 candle structure, intraday
   range-position, etc.) don't have clean daily equivalents. What's implemented is an
   8-component *D1 analog* (SMA stack, D1 candle streak, close-position-in-day-range,
   day-over-day ATR-scaled change, D1 trendline state, D1 volume confirmation, regime
   agreement, QQQ agreement), same general shape and thresholds (±25/±60, 2-day hysteresis,
   3-day EMA smoothing) but different inputs. A good D1 backtest result validates the RS/RW
   *thesis* on a swing timeframe -- it does NOT validate the actual M5 system that gets built
   in M5/M6.

2. **The trendline-breach trigger (03 §5) IS implemented at D1 cadence** (`compute_trigger()`
   in `engine_d1.py`) as the entry mechanism for Path A (05 §2, "buy the RS list the moment SPY
   breaches its trendline"). It originally appeared load-bearing (0 trades without it, 8 with it)
   -- but that 8-trade result depended on a real dtype bug (see "M3.5 status" below) that made
   `fresh_up`/`fresh_down` fire on *every* day within a breach, not just the first. With the bug
   fixed, the trigger correctly fires only 19 times in 5 years, and Path A no longer reliably
   produces trades in this universe. Path B (05 §3, per-symbol dip-arm) remains rare on its own
   too, as originally found. Net: neither path reliably produces trades at this universe size.

3. **`selection/scoring.py` redistributes spec weight W1 (M5 Rolling RRS, 25 pts) into W2 (D1
   RRS)**, since M5 RRS isn't available yet: D1 RRS is worth 45 pts instead of 20. The other
   weights (chart quality 15, divergence bonus 15, volume 10, headroom 10, consistency 5) are
   unchanged. Documented in the module docstring.

4. **`selection/gates.py` drops G2 (M5 RS) and G3 (VWAP)** -- no D1 equivalent -- and defers G9
   (QQQ cross-check) to the M5 build. G1 (price/ADV/float), G4 (HA continuation), G5 (SMA
   stack), G6 (headroom), G7 (volume), G8 (earnings, currently a no-op since
   `reference_overrides.yaml` is unpopulated) are all implemented.

5. **ADV liquidity gate recalibrated for IEX-only volume.** This was a real bug hunt, not a
   design choice: the spec's `min_adv_shares = 1,000,000` assumes full-market (SIP) consolidated
   volume. Alpaca's free tier serves IEX-only volume, which is ~2-3% of consolidated share
   volume for these names (confirmed against cached data -- e.g. median IEX volume for GS is
   ~76k/day, HON ~53k/day, despite both being genuinely deep mega-cap liquidity in reality).
   `BacktestConfig.min_adv_shares` defaults to `50_000` now, documented in `backtest/engine.py`.
   **Relative** volume signals (RVOL-style gates, `volume_ratio_d1`) are unaffected -- they
   compare the same feed to its own rolling average, so the scale cancels out. This matters again
   whenever a paid/SIP-scale data source is introduced later: the absolute threshold would need
   to move back toward the spec's original value.

6. **Shorts are structurally simpler and off by default** (`BacktestConfig.shorts_enabled =
   False`, matching spec 06's own recommended default). When enabled, shorts use gate+score+bias
   directly without the watchlist dip-arm state machine or a trigger-day fast path -- a
   simplification for this milestone, not a spec-accuracy claim.

7. **Position management in `backtest/engine.py` is a D1-adapted, simplified version of spec
   05 §4 / 06 §4**: hard stop -> bias-flip (with SPY stacked-candle confirmation) -> RRS failure
   (2-day) -> profit-take (target gain + HA continuation stall) -> EMA8-D1 trailing stop -> a
   pragmatic 40-day max-hold cap that has no spec equivalent (D1 has no explicit "time flat"
   rule). The spec's VWAP-loss exit rule is dropped (no D1 VWAP).

8. **The D1 walking skeleton's trades hold for 5-35 days** -- not the intraday, largely
   same-session round trips the real spec describes ("at least 5 really good trades throughout
   the day"). This is structural, not tunable: a backtest built on daily bars cannot produce a
   same-day exit, full stop. Confirmed after the M3.5 write-up, when the multi-day hold pattern
   was flagged as inconsistent with an intraday strategy -- correctly: the D1 engine was always a
   plumbing/thesis-direction check (deviation #1), never a preview of the real system's trade
   behavior, and further D1 threshold tuning (e.g. acting on the RRS-window finding) was
   deprioritized in favor of moving straight to M4/M5 real intraday data and cadence.

9. **M5-only indicators (`vwap.py`, `rvol.py`) require pre-filtered RTH-only input** (via
   `data.session.filter_rth()`, wired into `data.loader.load_minute_bars(rth_only=True)` by
   default) rather than filtering internally -- consistent with the existing pattern where D1
   indicators trust the loader to hand them clean, complete daily bars rather than re-deriving
   "is this a valid trading day" themselves. Alpaca's minute feed includes pre/post-market bars,
   confirmed against real cached data (a bar as early as 08:32 UTC, hours before the 13:30/14:30
   UTC open) -- unfiltered, these would contaminate VWAP's 09:30-anchored cumulative sum and
   RVOL's session-volume baseline.

10. **`indicators/rvol.py` keys its time-of-day baseline by real ET wall-clock time, not arrival
    order.** The first implementation used `groupby(session).cumcount()` ("bar N of the
    session") as a proxy for time-of-day, which works only if every session has the same bar
    count -- false for less-liquid names on an IEX-only feed (confirmed: coverage density ranges
    from ~99% for SPY down to ~21% for BKNG over the cached window, since IEX only reports a bar
    when a trade actually prints there that minute). Fixed before this became a silent
    correctness bug baked into the M5 selection engine: RVOL now keys on actual ET
    minutes-since-midnight, at the cost of the rolling baseline being NaN whenever any of the
    trailing 20 sessions lacks a bar at that exact minute (expected to be common for illiquid
    names/times, and preferred over a silently-misaligned value).

## Test coverage

65 tests as of the M3 checkpoint (this section describes those original test
categories; M3.5, M4, and M5 added more test files on top -- see their
respective sections below -- bringing the total to 124 as of this M5
checkpoint), all hermetic (no network calls, no real credentials needed):

- **Golden**: hand-computed fixtures for ATR, RRS (using the exact worked examples from
  `documents/A-New-Measure-of-Relative-Strength.md`), Heikin-Ashi, SMA stack, candle structure,
  headroom, trendlines.
- **Property** (hypothesis): ATR non-negativity, RRS scale-invariance, vectorized RollingRRS vs.
  an independently-written naive Python reference, SMA-stack category exhaustiveness.
- **No-lookahead / causality**: for every indicator, truncating history to "as of bar i" and
  recomputing reproduces exactly what the full-history run says bar i was. This is what actually
  validates the pivot confirmation-lag design in `headroom.py`/`trendlines.py` doesn't leak future
  bars -- `pivot_highs`/`pivot_lows` themselves are deliberately excluded (they're not causal in
  isolation by design; what's tested is that the indicators built on top of them are).
- **Ingestion integration**: cache-resume correctness (kill mid-backfill via a `BaseException` in
  a mocked client, confirm zero duplicate calls and no gaps on rerun) and error-vs-crash retry
  semantics (a caught `Exception` retries only that unit next run; an uncaught `BaseException`
  simulates a real process kill).

One test (`test_rolling_rrs_matches_naive_reference`, a hypothesis property test) was flaky --
failed on a generated input containing a value near `1e-95`, where `rtol`-only comparison breaks
down against near-zero floats. Fixed by adding `atol=1e-12` to the assertion; confirmed stable
across 5 reruns. Not a logic bug in `rolling_rrs()` itself.

## M3.5 status: a real bug, a corrected null result, and an open decision

While building the M3.5 study infrastructure, `tests/unit/test_studies.py` surfaced a
`DeprecationWarning` from `bias/engine_d1.py::compute_trigger`:
`fresh_up = b_up & ~b_up.shift(1).fillna(False)`. Shifting a bool Series introduces a NaN at the
boundary, which silently upcasts the Series to `object` dtype; `fillna(False)` then leaves plain
Python `bool` objects in an object-dtype array, and `~` on those invokes deprecated per-element
Python integer inversion (`~True == -2`, `~False == -1`) instead of numpy boolean negation. This
wasn't just cosmetic: empirically, `fresh_up` came out `True` on **268 of ~1251 days** under the
old code, including days that were clearly continuations of an existing breach, not fresh ones
(verified by hand around 2021-08-15, where `b_up` was `True` on both 08-12 and 08-15, and the old
code marked 08-15 "fresh" anyway). The bug made the SPY trendline-breach trigger fire far more
often than it should have. Fixed by using `b_up.shift(1, fill_value=False)` (no NaN introduced,
stays real `bool` dtype throughout) instead — see the code comment in `engine_d1.py`.

**This changes the M3 baseline result.** The "8 trades, 50% win rate, profit factor 1.73" figure
previously reported in this document was generated using the buggy, over-firing trigger. With the
fix, `compute_trigger` correctly fires only **19 times over the full 5-year window**, and
`python scripts/run_backtest_d1.py` now produces **0 trades**. That old result should be
considered invalid, not just superseded — it wasn't measuring the thing the D1 skeleton actually
does.

Root-caused this new result before treating it as "another bug to fix": it isn't one. Direct
inspection of gate pass rates across the 28-symbol universe (`gates.py`'s 7 D1 gates) shows each
gate passes reasonably often in isolation (price 100%, ADV 97%, RRS 18%, HA continuation 21%, SMA
stack 41%, headroom 50%, volume 40%), but requiring **all of them simultaneously** (by design —
this is the "Keeping It Really Simple" confluence philosophy) drops the joint pass rate to
**~0.83% of symbol-days** (~0.23 qualifying symbols per day, averaged over 28 symbols). Overlaying
that against a genuinely rare SPY trigger (19 fires in 5 years) or the Path B raw-RRS
zero-crossing dip-arm (also rare on its own, per the original M3 investigation) means the two
paths essentially never coincide with a qualified symbol in this universe. Checked directly: of
the 19 real trigger days, only 1 had even a single qualified/entry-eval symbol, and that one
still didn't convert to a trade.

This is very likely a **sample-size problem, not a thesis problem**: the real spec (`01`) scans
800-1,500 symbols; at the same ~0.83% joint qualification rate, that's 7-12 qualifying symbols on
an average day instead of ~0.23 — order-of-magnitude more chances for a trigger day or a dip-arm
sequence to land on a qualified name. The 28-symbol curated universe was sized for pipeline
validation (M0-M3), not for this kind of rare-confluence signal to show up in five years of data.

**What came out of M3.5's infrastructure work, independent of the trade-count problem**:
`backtest/studies/ablation.py`, `walk_away.py`, `rrs_sensitivity.py`, and
`scripts/run_validation_studies_m35.py` are built, tested (`tests/unit/test_studies.py`), and run
cleanly end-to-end against real cached data.

## Universe expansion (28 -> 130 symbols) and M3.5 results

Chose to expand the curated universe first (cheapest lever, no code changes, closest to the real
spec's design) rather than loosen gates or defer the thesis check. `config/universe.yaml` grew
from 28 to 130 symbols (SPY/QQQ + 128 trade symbols across the same 11 GICS sectors, sized
roughly proportional to real-world sector weights). One casualty found during backfill: **IPG
(Interpublic Group) was acquired by Omnicom and delisted in Nov 2025** — its truncated history
silently shrank the whole aligned trading calendar (`_align_calendar`'s intersection) from 1251
to 1104 days for *every* symbol, discarding 6 months of 2025-2026 data universe-wide. Swapped IPG
for CHTR (Charter Communications, still listed, full history) and reconfirmed all 130 symbols
have identical 1251-row coverage before re-running. Worth remembering for any future universe
change: check `select symbol, count(*) from bars group by symbol order by count(*) asc` before
trusting a backtest window, since one delisted/merged symbol degrades the *entire* aligned
calendar, not just its own history.

**Re-running `scripts/run_backtest_d1.py` on the 130-symbol universe produces 4 trades** (was 0 at
28 symbols, confirming the sample-size diagnosis) — win rate 25%, profit factor 0.18, total PnL
-$229. Still a thin sample, but now non-empty, and the M3.5 studies ran on real data:

- **Walk-away analysis (08 §3.2)**: 437 `IDLE -> QUALIFIED` entry signals (up ~4.2x from 104 at
  28 symbols, consistent with the ~4.6x universe-size increase — a good sanity check that the
  qualification mechanism itself scales linearly with candidate count, as expected). Mean MFE ≈
  1.91R, mean MAE ≈ -1.60R over a 20-day hold, essentially unchanged from the 28-symbol run —
  this distribution looks like a property of the *signal*, not an artifact of universe size. But
  realized trades average **-0.11R**, well below the available MFE — reinforcing the spec's own
  point that exits, not picks, are likely where this system is leaving the most value on the
  table (or losing it).
- **Gate ablation (08 §3.1)**: all 5 runs (baseline + disable-bias/rrs/ha/sma) produced the exact
  same 4 trades — disabling any single hard rule didn't unlock a single additional trade. This
  means none of {bias, RRS, HA continuation, SMA stack} is the actual binding constraint on trade
  frequency in this universe; the bottleneck is elsewhere (headroom, volume, ADV, or the
  trigger/dip-arm *timing* mechanism itself, none of which were ablated). The rule-count bucket
  table is degenerate (100% of trades satisfy all 4 rules; no data in buckets 0-3), so the
  spec's monotonicity hypothesis remains untested — a real finding, just not the one 08 §3.1
  expected to produce.
- **RRS sensitivity sweep (08 §3.3)**: this is where 130 symbols paid off — real variation with
  3-7 trades per cell instead of 0-2. `window=3` was strictly better than the M3 default
  `window=5` across every threshold/basis combination tested (profit factor 0.93-2.03 vs. 0.18-0.27,
  total PnL mostly +$600 vs. -$140 to -$229), and `window=8` was catastrophic (profit factor
  0.02-0.21, PnL -$2,213 to -$2,244). This is the opposite of the spec's hoped-for "broad and
  stable" result — it's a sharp, one-directional dependence on window choice — but it's also a
  concrete, actionable lead: **`RRS_D1_WINDOW` (currently 5, in `selection/features.py`) may be
  a better default at 3**, worth testing as a new baseline before further tuning. Small per-cell
  sample sizes (3-7 trades) mean this isn't proof, but the consistency of the direction across
  all three thresholds and both rolling/raw bases makes it more likely a real effect than noise.

**Bottom line for the "is the thesis real" checkpoint**: still can't fully answer it — 4 trades is
too few for the primary 08 §2 metrics to mean much on their own. But two independent signals point
the same direction: (a) the walk-away MFE/MAE distribution is stable and favorable across both
universe sizes, and (b) a shorter RRS window produces a real, consistent improvement. Neither
proves the thesis; both are more consistent with "there's a real, recoverable signal that the
current D1 gate/window/timing calibration is underexploiting" than with "there's nothing here."

**Decision made after this checkpoint**: don't invest further in D1 threshold tuning (e.g. acting
on the `RRS_D1_WINDOW=3` finding). Inspecting `reports/d1_backtest/trades.csv` directly surfaced
that every D1 trade holds for 5-35 days (see deviation #8) — a fundamentally different trade
shape than the actual intraday spec, which the D1 engine was always meant to approximate only
directionally (deviation #1), not preview. Better use of effort: move straight to M4 (real
minute-bar data, done) and M5 (the real intraday cadence and gate/score/trigger logic), where a
good or bad result actually says something about the system being built, rather than continuing
to calibrate a proxy whose trade timing doesn't resemble the target strategy at all.

## M5: full intraday market-bias + stock-selection engines

Built the real M5-cadence system (algo-spec 03/04) on top of the D1 walking skeleton and the
M5-only indicators M4 built ahead of schedule (VWAP, RVOL, Laguerre RSI). This milestone stops at
"engine functions exist, are unit tested, and are documented" — the same boundary M3 drew around
`bias/engine_d1.py`/`selection/*.py` before `backtest/engine.py` wired them together. **No backtest
replay loop, no order execution, no position management were built here** — that's M6. Per §7 of
the spec, the scheduled-event blackout only gates new *entries* ("bias keeps computing throughout")
— an M6/algo-layer concern, so `bias/engine.py` doesn't implement it.

**What was built:**

- `data/resample.py` — `resample_ohlcv(df, freq, closed="left")` aggregates the warehouse's raw
  1-minute bars up to true 5-minute (M5) bars, and M5 bars up to hourly (H1) bars. This step exists
  because the warehouse only stores 1-minute bars (what Alpaca actually returns), but the spec's M5
  math is calibrated for 5-minute spacing: `RollingRRS_M5`'s window `L=12` means "1 hour" only if
  each bar is 5 minutes, Laguerre RSI's `gamma=0.5` default decays at a 5-minute rate, and trendline
  pivot spacing assumes 5-minute bars. Also provides `align_causal(source, target_index)` (generic
  causal forward-fill: target[t] sees the most recent source value at or before t, never later) and
  `align_daily_to_intraday(daily, intraday_index, shift=1)` (broadcasts a D1-indexed series onto an
  intraday index, shifted by one session by default so a session never sees its own not-yet-closed
  D1 row) — the two alignment primitives everything else in M5 is built on.
- `bias/buckets.py` + `bias/trigger.py` — the hysteresis bucket vocabulary/`apply_hysteresis` and
  `compute_trendline_trigger`, extracted from `bias/engine_d1.py` (pure refactor, cadence-agnostic
  logic that both the D1 and M5 engines now share instead of duplicating).
- `bias/daily_context.py` — `daily_context_series(spy_d1)`: the pre-open daily-context pass (03
  §2) — `regime_d1`, prior-day D1 high/low/close, and a `suspect_rally`/`suspect_selloff` breakout
  audit (a fresh D1 trendline breach within the trailing 3 sessions whose `follow_through()` check
  doesn't confirm). Deliberately **not** causally shifted — every column describes that row's own
  close-of-day state; callers align it onto intraday bars with `shift=1`.
- `selection/features_m5.py` — `compute_symbol_features_m5(...)`: the per-stock M5 feature
  DataFrame. `RRS_M5` uses `ATR(H1, 50)` (H1 bars resampled from the symbol's own and SPY's M5
  bars); VWAP and RVOL are computed on the raw 1-minute frame per 02 §3 ("from 1-min bars") and
  causally aligned onto the M5 index; LRSI runs directly on M5 closes; one-candle-wonder anti-pattern
  detection (a single M5 bar contributing >60% of the RRS window's total price change) and `gap_pct`
  are computed here; D1-only signals (`ha_cont_d1`, `sma_stack`, headroom, D1 RRS) are reused as-is
  from `selection/features.py` and broadcast onto the M5 index.
- `bias/engine.py` — `compute_raw_score`/`bias_series`: the full 8-component M5 bias engine (VWAP
  side, M5 candle structure, intraday day-range position, prior-day levels, M5 trendline state, M5
  volume confirmation, D1 regime agreement, QQQ agreement), EMA-3 smoothing, 2-bar hysteresis
  (reusing `bias/buckets.py`), the trendline-breach trigger (reusing `bias/trigger.py`), a `warmup`
  flag (True before 10:15 ET), and a `flip_flatten` signal for 03 §6's bias-flip rule (signal only —
  the actual flattening *action* is M6's job).
- `selection/gates.py` extended with the full G1-G9 set: `gate_vwap_long/short`,
  `gate_rrs_m5_long/short`, `gate_not_one_candle_wonder`, `gate_no_gap_exclusion`,
  `gate_benchmark_crosscheck_long/short` (G9, QQQ cross-check), `gates_pass_long_m5`/
  `gates_pass_short_m5` composing the full set.
- `selection/scoring.py` extended with `score_long_m5`/`score_short_m5`: the full un-redistributed
  7-weight table (04 §4), now that `RollingRRS_M5` (W1, 25 pts) is actually available, instead of
  the D1 walking skeleton's W1->W2 redistribution.
- `selection/watchlist.py` extended: `next_state_long`/`next_state_short` gained optional
  keyword-only `lrsi_prev`/`lrsi_now` params implementing 04 §6's "RRS crosses OR LRSI crosses"
  dip-arm OR-condition (omitting them reproduces the D1 caller's RRS-only behavior exactly); new
  `apply_trigger_bypass` implements the trigger-day direct-entry exception (a QUALIFIED symbol goes
  straight to ENTRY_EVAL on a matching bias-engine trigger, instead of waiting for its own dip).
  `build_tradeable_list` is unchanged — already cadence-agnostic, reused as-is from the D1 build.
- Roughly 34 new tests across all of the above (`test_resample.py`, `test_daily_context.py`,
  `test_features_m5.py`, `test_engine_m5.py`, `test_gates_m5.py`, `test_scoring_m5.py`,
  `test_watchlist_m5.py`), bringing the total from 90 (post-M4) to **124**.

**Real bugs found and fixed during this build** (all resolved, all reviewed) — these matter more
than the feature list above for anyone extending this code later:

1. **M1-open-label vs. M5/H1-close-label lookahead bug.** Raw 1-minute bars from the warehouse are
   open-labeled (timestamp = interval start); `resample_ohlcv`'s M5/H1 output is close-labeled
   (timestamp = interval end, via `label="right"`). Calling `align_causal` directly on a
   1-minute-cadence series (VWAP or RVOL computed on 1-min bars) against the M5 index let an M5 bar
   pick up a not-yet-closed 1-minute bar sharing its own timestamp — a genuine 1-minute-of-future-
   data leak. Fixed with a `_close_label` helper (shifts the 1-min series' index forward by one
   minute before aligning), applied everywhere VWAP/RVOL-on-1-min-bars gets aligned onto an M5
   index, in both `selection/features_m5.py` and `bias/engine.py`, for SPY, QQQ, and every traded
   symbol. Covered by a regression test (`test_vwap_side_does_not_leak_the_next_minute_bar` in
   `test_engine_m5.py`).
2. **`gap_pct` used SPY's prior close instead of the stock's own.** An early version of
   `features_m5.py` computed the overnight-gap anti-pattern check (04 §3, ">20% gap excluded for
   the day") against SPY's prior D1 close rather than the stock's own — meaning nearly every
   non-SPY-priced symbol would show a huge spurious "gap" and get permanently excluded. Fixed to use
   the stock's own D1 close, already available via the existing D1-feature-reuse pipeline
   (`d1_aligned["close"]`).
3. **H1-from-M5 resample hour-boundary misattribution.** Resampling M5 bars up to H1 bars (needed
   for `RRS_M5`'s `ATR(H1, 50)` input) initially used the same `closed="left"` bucketing as the
   M1->M5 step, but M5 bars are close-labeled, not open-labeled like M1 — so an M5 bar sitting
   exactly on an hour boundary got attributed to the *next* hour instead of the one it actually
   closed in. Not a lookahead violation (always prior real data), but a systematic ~5-minute-per-
   hour precision bug in the ATR50 input. Fixed by adding an optional `closed` parameter to
   `resample_ohlcv` (default `"left"`, backward compatible with the M1->M5 call site) and calling it
   with `closed="right"` specifically for the M5->H1 step.
4. **A genuine plan gap: `candle_structure.stacked_count` had no M5-RVOL override.** The M5 bias
   engine's candle-structure "stacked" component (c2) needed M5-cadence, time-of-day-adjusted RVOL
   instead of the D1-only `volume_ratio_d1` that `stacked_count` always computed internally — the
   plan's own M5 engine code assumed a `volume_ratio` override parameter existed on that function
   before it did. Fixed with an additive, backward-compatible optional parameter (defaults to
   `None`, preserving the existing D1 callers' exact behavior).
5. **Silent backward-compatibility break from parameter ordering.** Adding the new optional
   `lrsi_prev`/`lrsi_now` parameters to `next_state_long`/`next_state_short` *before* the
   pre-existing `min_list_score`/`min_hold_score` parameters would have meant two existing
   production call sites (which pass those two thresholds positionally) silently swallowing their
   real threshold values into the new LRSI parameters instead — masked only by coincidence (the
   default threshold values happened to match). Fixed by making all four trailing parameters
   keyword-only (a bare `*` in the signature), which also converts any *future* mistake of this kind
   into an immediate `TypeError` instead of a silent bug; both call sites updated to pass the
   thresholds as keywords.

**Disclosed, not fixed (deliberate simplifications, matching this project's "document, don't
silently approximate" norm — see `selection/scoring.py`'s module docstring):**

- W3's "+3 if 8-EMA(D1) preserved on all pullbacks in window" sub-bonus is not implemented (base
  HA-continuation-length scoring only).
- W7's "lowest tercile of candidates" cross-sectional ranking is not implemented; M5 scoring reuses
  the D1 precedent's per-symbol continuous-std formula instead, rather than introducing an unrelated
  cross-sectional ranking API for this one weight.
- W4's "(or holding flat, RRS >= 2)" alternate path to the full 15-point divergence bonus is not
  implemented — only the primary "stock moves opposite the market" path scores the full bonus; a
  flat-price/high-RRS candidate only gets the proportional (~8 pt) score. Deferred as a third
  documented simplification alongside the two above rather than inventing a threshold for "flat"
  with no clean spec-given definition.
- News-halt anti-pattern exclusion (04 §3) is not implemented — there is no live halt feed available
  for backtesting against historical Alpaca bars.

**Final whole-branch review (after all 9 tasks + fixes were individually approved)**: a
cross-cutting review across the full M5 diff (dispatched separately from the per-task reviews,
specifically to catch anything only visible at the whole-milestone level — e.g. was the
lookahead fix applied consistently everywhere it needed to be, not just in the one file where it
was first found) came back **"Ready to move to M6: Yes"**, no Critical or Important findings.
It flagged six Minor items worth M6's attention, folded into "Known limitations" below
(items 12-15) rather than repeated here.

## M6: event-driven M5 backtest engine + long/short algo

Built the actual intraday round-trip system on top of M5's engine functions (8
tasks, subagent-driven development against `docs/superpowers/plans/2026-07-03-m6-backtest-engine.md`,
progress ledger at `.superpowers/sdd/progress.md`). **All code is written, unit
tested (182 tests, up from 124 at the M5 checkpoint), and independently
reviewed.** Two real, blocking crashes were found and fixed running the first
real-data backtests (see "Real bugs found and fixed" below); with both fixed,
**a real, complete M6 backtest now runs end-to-end against the full cached
warehouse and produces a result: 0 trades over 1252 trading days.** See "Real
backtest run" below for the full result and a root-cause diagnosis of the
zero-trade outcome, done the same way this project root-caused D1's original
0-trade result at M3.5 (direct inspection of gate pass rates and trigger fire
counts, not assumption).

**What was built:**

- `algo/risk.py` (Task 1) — position sizing (`position_size`, `cap_shares`),
  ATR-based stop placement (`stop_price_long/short`), NEUTRAL-bias dynamic stop
  tightening (`neutral_tighten_stop_long/short`), bias/score size multipliers,
  and a `RiskManager` class enforcing daily/weekly loss limits and a
  consecutive-stop-out entry halt (algo-spec 07 §1-4).
- `backtest/broker_sim.py` (Task 2) — order-fill simulation: marketable-limit
  entry pricing (`entry_limit_price`), next-bar fill logic (`try_fill_entry`),
  and slippage (`apply_slippage`), matching 08 §1's fill/timing convention.
- `algo/long.py` (Task 3) — long-side entry qualification
  (`not_extended_long`, `confirm_trigger_entry_long` for Path A,
  `dip_quality_pass_long` for Path B) and the full exit-signal series
  (`rs_failure_long`, `vwap_loss_long`, `momentum_stall_long`,
  `market_flip_exit_long`), per algo-spec 05 §2-4.
- `algo/short.py` (Task 4) — the short-side mirror of Task 3
  (`not_extended_short`, `confirm_trigger_entry_short`,
  `bounce_quality_pass_short`, and the exit-signal series), plus
  `squeeze_guard_short` (a short-only violent-adverse-move guard with no long
  equivalent) and an intentionally unconditional `market_flip_exit_short` (no
  `flip_flatten` confirmation gate, unlike the long side) — both asymmetries
  are per algo-spec 06 §4's own design, not implementation gaps.
- `backtest/engine_m5.py` (Tasks 5-6) — `_prepare_m5`/`PreparedM5`: the
  per-symbol precompute layer, computing every feature/gate/score/exit-signal
  on each symbol's own native M5 index before reindexing onto a shared master
  calendar; `run_m5_backtest`/`BacktestConfigM5`/`PositionM5`/`TradeM5`/
  `BacktestResultM5`: the actual bar-by-bar event loop — order submission,
  fills, position management (all of 05 §4/06 §4's exit rules), risk-manager
  gating, and the long/short watchlist state machine wiring. This is the real
  intraday round-trip engine M5 was missing.
- `scripts/run_backtest_intraday.py` (Task 7) — the CLI entry point: loads the
  real warehouse (M1/M5/D1 bars for the full universe), runs
  `run_m5_backtest`, prints 08 §2 metrics + exit-reason breakdown, writes
  `reports/m5_backtest/trades.csv`/`equity_curve.csv`.
- `data/loader.py::load_universe_m1_bars` (Task 7) — a thin wrapper/alias of
  `load_universe_minute_bars`, named to match `load_universe_m5_bars`'s
  convention for the backtest engine's call sites.

**Real bugs found and fixed during the build** (all resolved, all reviewed,
per `.superpowers/sdd/task-{1..7}-report.md` and `progress.md`):

1. **Critical — SHORT-side fill condition used the wrong bar field
   (Task 2).** `broker_sim.try_fill_entry`'s SHORT branch checked
   `bar_low <= limit_price` (copy-pasted from the LONG branch) instead of
   `bar_high >= limit_price`. A short sells into strength, so the bar must
   trade *up* to the limit, not down — the bug produced phantom fills (a bar
   whose high never reached the limit still "filled") and missed fills (a
   fully marketable bar returned no fill at all). Found in code review, fixed
   to match the brief's own correct reference code, verified with two new
   regression tests reproducing both failure modes directly.
2. **Important — a `chop_ratio` window workaround was a misdiagnosed fix,
   not a real bug (Task 3).** The first implementation of
   `dip_quality_pass_long` called `chop_ratio(df_m5, window=window-1)` after
   observing the brief's own 6-bar test fixture return all-NaN at
   `window=6` — but that NaN was purely a test-fixture artifact (a
   `window`-bar fixture with zero prior trading history has no predecessor
   bar for `overlap_ratio`'s internal `shift(1)` to compare against; real
   production data always has real bars before any pullback window is
   evaluated). The `window-1` change would have created a real, silent
   production inconsistency: `bias/engine.py` already calls
   `chop_ratio(spy_m5, window=CHOP_WINDOW)` with no such adjustment,
   establishing this codebase's actual convention, and `-1` would have left
   `dip_quality_pass_long`'s mixed-candle check covering only 5 of its own
   declared 6-bar window while its other four sub-checks used the full 6.
   Reverted the window-1 workaround; gave the test fixtures one real leading
   warmup bar instead, matching how every other rolling-window indicator in
   this codebase is exercised.
3. **Important — zero test coverage of the precompute layer's #1 stated
   risk invariant (Task 5).** `_prepare_m5`'s central correctness rule —
   compute every per-symbol quantity on that symbol's own native M5 index
   *first*, and only reindex the *finished* output onto the shared master
   calendar *last* — had no test that actually exercised a genuinely gapped
   native-vs-master calendar (the fixture's AAPL and SPY M5 indices were
   bit-for-bit identical, making every `.reindex()` call a no-op). Fixed by
   adding a real thin-symbol regression test (a synthetic "THIN" symbol
   trading on only half the sessions, with an intra-session gap on the days
   it does trade). Verified the test actually catches the violation it's
   meant to catch by deliberately reintroducing the bug (reindexing before
   computing) and confirming the new test fails — with a genuinely
   informative failure mode: Wilder's ATR (`.ewm(adjust=False)`) doesn't
   just leave the gap blank, it silently carries a stale-but-plausible ATR
   value forward across the injected NaN gap.
4. **Two Critical bugs in Task 6's own hand-written reference code (found
   in review, not implementer error — this was the plan's highest-risk
   task):**
   - **Inverted trailing-stop clamp.** Algo-spec 05 §4.6/06 §4's literal
     formula is `max(EMA8(M5) - 0.25×ATR, entry)` for LONG once the trail
     trigger fires (mirrored with `min`/`+0.25` for SHORT). The code computed
     `min(trail, entry)` for LONG and `max(trail, entry)` for SHORT — backwards.
     The effect: the instant a trend extends far enough that `trail` first
     exceeds `entry` (exactly the normal case this rule exists to handle),
     the clamp discarded `trail` and substituted `entry`, permanently
     freezing the stop at breakeven — the trailing stop could never advance
     again no matter how far the trend ran. Fixed to match the spec's literal
     formula; verified via bug-injection (temporarily reverted the fix,
     confirmed the new regression test fails with the exit price landing at
     breakeven instead of the ~4+ points above it the fixed code produces,
     then restored the fix and confirmed the test passes).
   - **`slots_free`/`slots_free_s` counted across both books combined.**
     The available-entry-slot counters for the long and short books both used
     `len(positions)` — *all* open positions regardless of direction —
     instead of same-direction-only. With shorts enabled, an open LONG
     position wrongly ate into the SHORT book's slot budget (and the mirror
     bug the same for an open SHORT against the LONG book). Fixed by
     filtering `positions.values()` by direction before counting; verified
     via bug-injection the same way (reverted, confirmed a scripted
     same-bar starvation scenario produces zero SHORT trades under the bug
     and a real completed trade under the fix).
5. **Critical — `body_pct`/`overlap_ratio` (`indicators/candle_structure.py`)
   silently upcast a float64 Series to object dtype on any real zero-range
   (`high == low`) M5 bar, crashing the first full-scope real-data run
   (`commit c4fc237`).** Both functions guarded their division-by-zero case
   with `rng.replace(0, pd.NA)`. `pd.NA` cannot be held in a numpy `float64`
   array, so replacing even a single zero with `pd.NA` silently upcasts the
   *whole* Series to `object` dtype — `np.nan` does not have this problem.
   Once object-dtype, `chop_ratio`'s `.rolling(window).mean()` (called from
   `bias/engine.py`'s shared candle-structure score component,
   `algo/long.py::dip_quality_pass_long`, and
   `algo/short.py::bounce_quality_pass_short`) crashes immediately with
   `pandas.errors.DataError: No numeric types to aggregate`. This is real, if
   uncommon, in actual market data — confirmed directly against the cached
   warehouse: SPY's own 5-year M5 history has 34 such bars (e.g. the
   half-day session after Thanksgiving 2021, several isolated single-print
   bars in 2024-2025) — and never occurs in the hand-built synthetic OHLCV
   fixtures used throughout Tasks 1-7's unit tests, which always vary price
   continuously. It crashed both the full 128-symbol/5-year run and an
   earlier reduced-scope 10-symbol/~4-month run (a different call site: the
   reduced run's crash came from `algo/long.py::dip_quality_pass_long` on a
   traded symbol's own bars, not from SPY). Fixed by using `np.nan` instead
   of `pd.NA` in both places, keeping the Series `float64` throughout; a
   regression test with a real zero-range-bar fixture verifies `body_pct`,
   `overlap_ratio`, `chop_ratio`, and `stacked_count` all stay
   float64/non-object, don't raise, and correctly produce `NaN` (not `0`/
   `inf`) at the zero-range bar's own row while other rows stay finite —
   verified against the pre-fix code to confirm it reproduces the original
   crash.
6. **Critical — `compute_raw_score`'s c8 (QQQ-agreement) component assumed
   QQQ and SPY's M5 bar indices were identical, crashing on real market data
   (`commit 4cc9a44`).** `bias/engine.py::compute_raw_score` computed
   `qqq_diff_pct` from `qqq_m5["close"]` directly, without reindexing it onto
   `spy_m5.index` the way `qqq_vwap` already was via `align_causal`. In every
   synthetic fixture used throughout Tasks 1-8's unit tests, QQQ and SPY's M5
   indices happened to be bit-for-bit identical, so this was never exercised.
   Real QQQ/SPY M5 bar indices differ (each can be missing bars the other
   has, on Alpaca's IEX-only feed), so the raw subtraction auto-aligned onto
   the *union* of both indices, desyncing `qqq_above` from `spy_above`, and
   `spy_above == qqq_above` raised `ValueError: Can only compare
   identically-labeled Series objects` the instant the two symbols' indices
   diverged — which happens fast across 5 years of real data. This resolves
   the known-limitation item flagged in M5's own final review (see item 14
   below, now closed): "`bias/engine.py`'s docstring documents `qqq_m5` must
   share `spy_m5`'s index, but nothing enforces this at runtime." Fixed by
   explicitly `.reindex(spy_m5.index)`-ing `qqq_m5["close"]` before the
   subtraction (the same pattern `selection/features_m5.py`'s
   `spy_close_aligned` already used) — a strict reindex, no `.ffill()`, so a
   `spy_m5` bar QQQ has no native bar for now correctly reads as "QQQ
   disagrees" (`qqq_above=False` from the NaN comparison) rather than
   crashing or silently defaulting to "agrees."

**Disclosed, not fixed (deliberate simplifications, matching this project's
document-don't-silently-approximate norm — full detail in the referenced
modules' own docstrings):**

- Algo-spec 07 §6's kill switches are not implemented — a live-trading-only
  concern (halting new order submission on broker/data-feed errors), out of
  scope for a historical backtest.
- `algo/risk.py`'s stops are ATR-only (`stop_price_long/short`); 07 §3's
  swing-low/swing-high alternative stop placement is not implemented.
- `algo/long.py`/`algo/short.py` translate the spec's discretionary
  dip/bounce-quality language ("healthy pullback," "wimpy bounce") into a
  concrete indicator vocabulary (`chop_ratio`, `RVOL`, pullback depth in ATR,
  `stacked_count`, VWAP-held) — a judgment call documented in each function's
  own docstring, not a literal restatement of the spec's prose.
- The same three M5-scoring simplifications carried over from the M5
  checkpoint (W3's EMA8-pullback sub-bonus, W7's cross-sectional-tercile vs.
  per-symbol formula, W4's missing flat/RRS≥2 alternate bonus path) and the
  deferred news-halt anti-pattern exclusion are unchanged by M6 — see the M5
  section above.

**Real backtest run — a real result: 0 trades.** The two crashes above
(`c4fc237`, `4cc9a44`) blocked every real-data run until both were fixed. With
both fixed, `python scripts/run_backtest_intraday.py` now runs end-to-end
against the full cached warehouse (128 trade symbols + SPY/QQQ, full
5-year/1252-trading-day window, ~97,257 M5 bars) without crashing. Full scope
precompute + backtest took **~15-16 minutes wall-clock** in this run (started
~14:16, finished ~14:32) — useful concrete data for anyone wondering, in a
future run, whether the process has hung. Raw output:

```
Running M5 backtest: 128 symbols, shorts_enabled=False

0 trades over 1252 trading days
  n_trades: 0
  win_rate: None
  profit_factor: None
  avg_win: None
  avg_loss: None
  avg_win_loss_ratio: None
  max_drawdown_pct: 0.0
  trades_per_day: 0.0
  total_pnl: 0.0

Wrote trade log to reports/m5_backtest/trades.csv
```

`reports/m5_backtest/equity_curve.csv` has 97,257 rows (matches the expected
M5 bar count: 1252 days x ~78 bars/day). `reports/m5_backtest/trades.csv` has
only a header row — genuinely zero fills, not a reporting artifact.

**Root-caused before treating this as either "nothing works" or "fine, ship
it"** — following this project's own M3.5 precedent (see that section above),
which found D1's original 0-trade result was a sample-size/gate-confluence-
rarity problem, not a bug, only after directly inspecting gate pass rates and
the trigger's fire count. Did the same here: a standalone diagnostic script
(`.superpowers/sdd/task9_diagnostic.py`, not committed to the codebase),
using `rs_spy.backtest.engine_m5._prepare_m5` directly to reuse every
already-computed gate/score/feature series rather than recomputing by hand,
against real cached data for 7 large, liquid, actively-covered symbols
(AAPL, MSFT, NVDA, AMD, JPM, XOM, UNH) over the full 5-year window
(~97,256 M5 bars each):

- **Bias reaches BULL territory plenty — it is not the bottleneck.** SPY's
  bias bucket reads BULL/STRONG_BULL 38.86% of all M5 bars (37,795/97,256),
  and holds for the actual 05 §1.2 long precondition — >=2 consecutive bars,
  `bias_ok_long` in `engine_m5.py` — 35.94% of the time (34,952/97,256). Not
  stuck in NEUTRAL/CHOP.
- **The trigger fires often, not rarely — also not the bottleneck.**
  `LONG_TRIGGER` fires 1,591 times and `SHORT_TRIGGER` 561 times over the
  5-year window (vs. D1's SPY trendline trigger firing only 19 times in the
  same window at M3.5) — an order of magnitude more opportunities for a
  trigger-day direct-entry (04 §6) than the D1 skeleton ever had.
- **The real bottleneck is the same "rare confluence" shape M3.5 found at
  D1, now with more simultaneous gates required.** Per-symbol M5 gate pass
  rates (isolated, on each symbol's own native calendar, `gates_pass_long_m5`'s
  9-gate set) across the 7 sampled symbols:

  | gate | AAPL | MSFT | NVDA | AMD | JPM | XOM | UNH |
  |---|---|---|---|---|---|---|---|
  | price>=$10 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
  | ADV (M5-cadence, see below) | 1.79% | 0.12% | 56.21% | 0.66% | 0.02% | 0.00% | 0.03% |
  | rrs_d1>=1 | 11.85% | 11.77% | 16.53% | 15.16% | 17.56% | 27.34% | 22.61% |
  | ha_cont_d1>=2 | 21.51% | 20.86% | 24.51% | 21.03% | 25.21% | 24.62% | 19.42% |
  | sma_stack=ABOVE_ALL | 40.74% | 35.63% | 50.32% | 31.26% | 53.39% | 44.16% | 27.95% |
  | headroom_long | 49.18% | 49.08% | 49.51% | 61.56% | 48.12% | 43.95% | 48.56% |
  | volume_ratio_d1>=1 | 39.73% | 40.78% | 40.97% | 40.13% | 40.82% | 40.14% | 38.79% |
  | rrs_m5>=1 | 3.93% | 3.34% | 3.79% | 4.30% | 4.62% | 7.81% | 7.61% |
  | vwap (close>vwap_m5) | 51.47% | 51.00% | 52.87% | 50.34% | 52.34% | 52.04% | 50.80% |
  | not_one_candle_wonder | 31.77% | 31.93% | 32.38% | 32.13% | 32.39% | 33.15% | 32.52% |
  | no_gap_exclusion | 99.92% | 99.92% | 99.84% | 99.84% | 99.92% | 99.92% | 99.92% |
  | **JOINT (all 9)** | **0.00%** (0) | **0.00%** (0) | **0.00%** (4) | **0.02%** (15) | **0.00%** (0) | **0.00%** (0) | **0.00%** (0) |

  No single gate is an outright always-fail wall the way, say, a
  miscalibrated threshold would produce — `rrs_m5` is the most restrictive at
  3.3-7.8%, but every other gate passes at least ~20-50% of the time in
  isolation. Requiring all 9 simultaneously (the same "Keeping It Really
  Simple" confluence-by-design philosophy M3.5 found at D1, now with 9 hard
  gates instead of 7) drives the joint pass rate to **0.00-0.02% of M5
  bars** across every symbol tested — 0 to 15 bars out of ~97,000 per symbol
  over 5 years.
- **A genuine, specific miscalibration found along the way, but confirmed
  NOT to be the dominant cause:** `gate_adv` (G1, the liquidity/"average
  daily volume" gate) is fed `df_m5_native` — the symbol's own M5 bars — by
  `_prepare_m5`, so its `df["volume"].rolling(20).mean()` computes a rolling
  ~100-minute (20 M5-bar) average of *5-minute-bar* volume, not a genuine
  20-*day* average daily volume, even though `_prepare_m5` already computes a
  correct daily-cadence ADV series (`adv20_native`, used elsewhere for
  `risk.cap_shares`'s position-sizing cap) that the gate never uses. This
  produces wildly inconsistent, cadence-confused pass rates across
  comparably liquid names (0.00% for XOM, 0.02% for JPM, vs. 56.21% for
  NVDA) rather than a sensible, roughly-uniform liquidity screen. However,
  ADV isn't in `HARD_RULE_NAMES`'s ablatable set, so its exact contribution
  was checked by hand: ANDing every *other* gate together (excluding ADV)
  still only reaches 0.00-0.02% jointly (4-23 bars per symbol) — barely
  different from the 9-gate joint rate above. **The ADV gate's cadence
  mismatch is real and worth fixing, but the other 8 gates' confluence
  alone already drives the joint rate to the same negligible level** — this
  is not a single-gate miscalibration story, it's a many-gates-at-once
  story. See "Known limitations" item 16 below for the actionable follow-up.
- **Watchlist state machine confirms the gate-confluence story directly, not
  just implies it.** Simulating `next_state_long` bar-by-bar (same config as
  the real backtest) for all 7 sampled symbols over the full 5-year window:
  6 of 7 (AAPL, MSFT, NVDA, JPM, XOM, UNH) never left `IDLE` even once. AMD
  reached `QUALIFIED` for 15 bars total but never reached `DIP_ARMED` or
  `ENTRY_EVAL`: `next_state_long` only advances `QUALIFIED -> DIP_ARMED` on
  an RRS/LRSI cross *while gates are still passing on that same bar*
  (`holds = gate_pass and score_ok`), and AMD's isolated few-bar
  gate-passing windows never happened to coincide with a dip-arm crossing.
  This is the M5-cadence analog of M3.5's own finding ("of the 19 real
  trigger days, only 1 had even a single qualified/entry-eval symbol, and
  that one still didn't convert to a trade") — direct confirmation that the
  bottleneck is confluence rarity all the way through the pipeline, not a
  single broken stage.

**Interpretation: this looks like the same sample-size/confluence-rarity
story M3.5 found at D1, not a new thesis-breaking result — but it is a more
severe version of it.** At D1, 130 symbols x ~0.83% daily joint-gate pass
rate produced 4 trades in 5 years. At M5, the sampled joint pass rate is
1-2 orders of magnitude smaller (0.00-0.02% of *bars*, on a calendar with
~78x more bars per trading day than D1 has rows), across a comparably-sized
128-symbol universe. That is consistent with 0 trades without implying the
M5 engine logic itself is broken (bias reaches bull territory correctly,
the trigger fires plausibly often, the gates each pass individually at
sane, non-degenerate rates) — but it also means the M3.5 lesson ("expand the
universe, the signal is probably real but under-sampled") may not be enough
on its own at M5 cadence, since the joint-pass-rate gap versus D1 is much
larger than the ~4.6x universe expansion that fixed D1's 0-trade result.
This diagnostic only covered 7 of 128 symbols (a deliberate scope reduction
per this task's own instructions — the full 128-symbol precompute is slow);
the pattern was completely consistent across every symbol sampled, but a
full-universe version of this same gate-pass-rate/watchlist-state audit
(extending `backtest/studies/ablation.py` past the D1-only {bias, rrs, ha,
sma} set it currently ablates, per Known limitation #5) is real, concrete,
un-started M7 work, not yet done here — see "Known limitations" item 16 and
"Next: M7" below.

**Final whole-branch review (opus, range `a28bca7..d7a4571`, 18 commits)**: came
back **"Ready to move to M7: Yes, with caveats,"** no unresolved Critical findings.
Independently re-traced the position-management rule order on both books against
the plan's Global Constraints (confirmed correct) and specifically stress-tested
whether anything in the wiring — not just gate confluence — could produce a false
zero-trade result; found none (the pipeline is proven live by a green end-to-end
entry→fill→trail→exit test, and every prepared series shares one calendar index
with no off-by-one). One finding it raised (the 10:15 ET entry-window lower bound
looked unenforced in `engine_m5.py` since no literal `10:15` constant appears in
that file) was checked and confirmed a false positive: `in_entry_window` composes
`bias_df["warmup"]` (`~warmup` = at/after 10:15 ET, defined via `WARMUP_CUTOFF` in
`bias/engine.py`) with the 15:30 cutoff, and an empirical check confirms the
resulting window is exactly 10:15-15:30 ET. Two genuine Minor findings — a stale
"180 tests" count (now corrected to 182 throughout this document) and the dip-arm
cross-detection edge case — are folded into "Known limitations" below (item 23)
rather than repeated here.

## Known limitations / open risks (unchanged from the plan except where noted)

1. IEX vs. consolidated-tape data divergence -- see deviation #5 above; larger than "minor" for
   absolute-scale gates specifically, immaterial so far for RS/RW's ratio-based math.
2. Curated-universe survivorship bias -- was expected to inflate results vs. a true point-in-time
   universe scan; the M3.5 finding above shows the opposite failure mode also matters (a small
   universe can starve a rare-confluence signal of any samples at all, not just bias the ones it
   finds). Also: a single delisted/merged symbol (see IPG above) can silently truncate the whole
   aligned backtest calendar -- worth a coverage sanity-check after any universe change.
3. `reference_overrides.yaml` earnings blackout is entirely unpopulated (all empty lists) -- the
   earnings gate (G8) is currently a no-op.
4. No `algo/risk.py` yet -- position sizing lives inline in `backtest/engine.py` (fixed-fractional
   against a 1.5xATR stop, 0.5% risk/trade), not yet the dedicated module the plan describes for
   M6.
5. Gate ablation (08 §3.1) still isn't informative even at 130 symbols/4 trades -- disabling
   {bias, rrs, ha, sma} individually never unlocked a new trade, meaning the real bottleneck on
   trade frequency is one of the un-ablated gates (headroom/volume/ADV) or the
   trigger/dip-arm timing mechanism, not tested by the current ablation set.
6. RRS window sensitivity (08 §3.3) suggests the M3 default (`RRS_D1_WINDOW=5`) may be
   miscalibrated (`window=3` outperformed it on every swept threshold/basis) -- deliberately not
   acted on; deprioritized in favor of M4/M5 (see "Decision made after this checkpoint" above).
   `RRS_M5_WINDOW` (now built, `selection/features_m5.py`, spec value L=12) hasn't had the same
   sensitivity-sweep methodology applied to it yet -- worth doing once M6's backtest engine exists
   to actually run it against.
7. `indicators/vwap.py` and `indicators/rvol.py` require pre-filtered RTH-only input and will
   silently produce wrong (not erroring) results if called on raw minute bars that still include
   pre/post-market rows -- always go through `data.loader.load_minute_bars()` (RTH-filtered by
   default) rather than querying `bars` directly for `timespan='minute'`.
8. `indicators/rvol.py` will be NaN often for illiquid names/times (see deviation #10) -- the M5
   gates (`selection/gates.py::gate_volume`/`gate_rrs_m5_*`) handle this the same way pandas
   comparisons naturally do (`NaN >= threshold` is `False`), so a NaN RVOL/RRS_M5 value fails its
   gate rather than raising or silently passing -- not a special-cased fallback, just confirmed
   behavior worth knowing about before assuming a NaN means "no signal" rather than "gate fails."
9. `data/warehouse.duckdb` is now ~3.2GB (44.1M minute rows dominate). Fine for local dev; worth
   knowing before assuming this fits an ad hoc backup/sync workflow.
10. M5 scoring's three documented simplifications (W3's EMA8-pullback sub-bonus, W7's
    per-symbol-continuous vs. cross-sectional-tercile formula, W4's missing "flat/RRS>=2" alternate
    bonus path) and the deferred news-halt anti-pattern exclusion (no live halt feed available for
    backtesting) -- see the M5 section above. None are silent: all four are documented in
    `selection/scoring.py`'s module docstring (the first three) or this file (news-halt).
11. `bias/engine.py`'s §7 scheduled-event blackout is not implemented (bias computation runs
    continuously, per spec; blackout is an entry-gating concern for M6's algo layer, not an engine
    output) -- M6 needs to add this gate before real entries are evaluated around scheduled events
    (FOMC, CPI, etc.).
12. **M5's volume *gate* (`gates.py::gate_volume`, reused inside `gates_pass_long_m5`/`_short_m5`)
    still checks D1's `volume_ratio_d1`, not the new intraday `rvol_m5`** -- `rvol_m5` currently
    only feeds `scoring.py`'s W5 weight, not any G-series hard gate. This may be intentional (the
    D1 "hard rule" volume filter carried forward as-is into M5), but it means the gate doesn't use
    intraday relative volume at all. Confirm against algo-spec 04 §2 before M6 relies on this gate,
    and document the decision either way alongside the M5 scoring simplifications above.
13. **`compute_raw_score`'s M5 ATR-14, trendlines, and candle-structure metrics run on the
    continuous, concatenated cross-session M5 series** (mirroring the D1 engine's own treatment),
    so an overnight gap enters true-range/pivot-detection math like any other bar-to-bar move. Not
    wrong, and internally consistent with the D1 precedent, but a genuine intraday subtlety worth
    M6 knowing about before trusting trigger/breach sensitivity right around a session's open.
14. ~~`bias/engine.py`'s docstring documents `qqq_m5` must share `spy_m5`'s index, but nothing
    enforces this at runtime`~~ **RESOLVED (`commit 4cc9a44`).** This was not just a theoretical
    risk -- it crashed the real M6 backtest (`ValueError: Can only compare identically-labeled
    Series objects`) the first time it was run against 5 years of real QQQ/SPY M5 data. Fixed by
    explicitly reindexing `qqq_m5["close"]` onto `spy_m5.index` before use in c8. See the M6
    section's "real bugs found and fixed" list above for full detail.
15. Two different RVOL computation paths exist in the M5 code and are correct-but-worth-noting:
    `features_m5.py` computes RVOL on 1-minute bars then aligns onto M5 (per 02 §3/4's literal
    "1-min bars" language), while `bias/engine.py` computes it directly on M5 bars. Because
    `rvol()` is session-*cumulative* volume vs. a same-clock-time baseline, both approaches yield
    the same result for liquid symbols (SPY/QQQ) -- confirmed not a bug or inconsistency during the
    final whole-branch review -- but a future reader could mistake this for drift and "fix" one to
    match the other. Worth a one-line code comment noting the equivalence explicitly.
16. ~~Blocking: `indicators/candle_structure.py::overlap_ratio`/`chop_ratio` crashes on any real
    M5 dataset containing a zero-range (`high == low`) bar~~ **RESOLVED (`commit c4fc237`)** -- see
    the M6 section's "real bugs found and fixed" list above for full detail. With this and item 14
    both fixed, the real M6 backtest now runs end-to-end and produces a real result: **0 trades
    over 1252 trading days, 128 symbols** (see "Real backtest run" in the M6 section above).
    **New, specific, actionable finding for M7, in place of this now-resolved item:** the
    diagnostic behind that 0-trade result found `gates.py::gate_adv` (G1, the liquidity/"ADV"
    gate) is fed M5-cadence bars by `_prepare_m5` -- its `df["volume"].rolling(20).mean()` computes
    a rolling ~100-minute (20 M5-bar) average of 5-minute-bar volume, not a genuine 20-*day*
    average daily volume, even though `_prepare_m5` already computes a correct daily-cadence ADV
    series (`adv20_native`) for `risk.cap_shares`'s position-sizing cap that the gate itself never
    uses. Confirmed via a 7-symbol/5-year sample: this produces wildly inconsistent pass rates
    across comparably-liquid names (0.00% XOM, 0.02% JPM, 56.21% NVDA) instead of a sensible,
    roughly-uniform liquidity screen -- a real miscalibration, worth fixing before M7 draws
    conclusions from any gate-ablation study that includes G1. **However, checked directly and
    confirmed this is not the dominant cause of the 0-trade result**: ANDing every other M5 gate
    together (excluding ADV) still only reaches a 0.00-0.02% joint pass rate per symbol (4-23 bars
    out of ~97,000, barely different from the full 9-gate rate) -- the same many-gates-simultaneously
    confluence rarity M3.5 found at D1 is the dominant story here, not this one gate. See "Real
    backtest run" in the M6 section above for the full per-gate breakdown and the watchlist-state
    confirmation (6 of 7 sampled symbols never left `IDLE`, the 7th reached `QUALIFIED` but never
    `DIP_ARMED`/`ENTRY_EVAL`, over the full 5-year window). **RESOLVED (`commit ad2ee2b`/`b2b6eaa`)**
    -- `gate_adv` now accepts a precomputed `adv` series, threaded through as `adv20_native` from
    `_prepare_m5`. Full-universe `adv` pass rate post-fix: mean 94.0%, min 1.2% (see "M7
    pre-work..." section below). Confirmed real, non-cosmetic effect: the fix alone moved the real
    backtest from 0 to 3 trades. Confluence rarity across the *other* 8 gates remains the dominant
    story, exactly as predicted here.
17. 07 §6's kill switches (broker/data-feed-error entry halts) are not implemented -- a
    live-trading-only concern, does not apply to historical backtesting, not planned for any
    future milestone unless live trading is pursued.
18. `algo/risk.py`'s stop placement is ATR-only (`stop_price_long/short`) -- 07 §3's
    swing-low/swing-high alternative stop is not implemented. Worth revisiting once real trade
    data exists (post item #16's fix) to see whether ATR-only stops are getting run over by
    structure the swing-based alternative would have avoided.
19. `algo/long.py`/`algo/short.py`'s dip/bounce-quality functions
    (`dip_quality_pass_long`/`bounce_quality_pass_short`) are a documented translation of the
    spec's discretionary language ("healthy pullback," "wimpy bounce") into a concrete indicator
    vocabulary (chop ratio, RVOL, ATR-scaled depth, stacked-candle count, VWAP-held) -- a judgment
    call, not a literal restatement of 05 §3/06 §3's prose. See each function's own docstring.
20. **The identical inverted-trailing-stop-clamp bug found and fixed in `backtest/engine_m5.py`
    during Task 6 (see M6 section above) also exists in the pre-existing D1 engine
    (`backtest/engine.py`, ~L279-283)** -- this is in fact where the M6 reference code's copy of
    the bug originated from, believed at the time to be matching established, working precedent.
    Flagged during Task 6's review as out of scope to fix there (separate review history, D1
    milestone already closed); still open. Needs its own follow-up fix -- the D1 backtest's
    trailing-stop behavior on any trade that ran far enough to trip the trail trigger has been
    silently freezing at breakeven this whole time, the same way the M6 bug did before its fix.
21. **A narrow, untested `slots_free` mirror-direction scenario remains open** (Task 6's
    fix-round review, non-blocking). The fix for the cross-book slot-miscounting bug (M6 section
    above) is applied symmetrically to both `slots_free` and `slots_free_s` and is believed
    correct either way, but a claim made during the fix that the *mirrored* starvation direction
    (an open SHORT position starving the LONG book) is "provably unreachable" was found by the
    reviewer to be overstated: a thin/gappy symbol with a NaN bar landing on the exact bias-flip
    bar is a real, if narrow, path to that same starvation pattern in the other direction. No
    dedicated regression test exists for this scenario yet -- worth picking up in M7 or a future
    hardening pass.
22. `tests/integration/test_run_backtest_intraday_script.py` never actually invokes
    `scripts/run_backtest_intraday.py`'s `main()` directly -- it re-implements the script's wiring
    inline instead, so a typo introduced directly in the script file would not be caught by the
    test suite. Minor, pre-existing in the Task 7 brief, not an implementer fault; not blocking,
    but worth tightening before relying on this test as a safety net for script-level changes.
23. ~~**Found during the final whole-branch review, minor, does not affect the 0-trade result:**
    `run_m5_backtest`'s dip-arm cross detection (`rrs_prev`/`lrsi_prev` via `.iat[i-1]` on the
    *reindexed* `features` frame, `engine_m5.py` ~L466-469) is the one exit/entry-signal series in
    the event loop computed on reindexed data rather than following this milestone's own
    native-first-then-reindex-last convention (every other cross series --
    `rs_failure_long/short`, `vwap_loss_long/short`, `momentum_stall_long/short` -- is computed on
    each symbol's native M5 index in `_prepare_m5`, then reindexed). Consequence: for a thin/gappy
    symbol whose immediately-preceding master-calendar bar has no native data (a real, anticipated
    scenario for less-liquid IEX-fed names -- see deviation #10), the RRS/LRSI "previous" value
    reads NaN and the crossing comparison silently evaluates `False`, suppressing that bar's
    dip-arm advancement rather than comparing against the symbol's own last real prior reading.
    Confirmed to have no effect on the real 0-trade result documented above (the sampled
    large-cap/liquid names have effectively continuous M5 coverage), but worth fixing -- or at
    least being aware of -- before M7 expands the universe to thinner names specifically hoping a
    larger sample surfaces trades: this bug would silently work against exactly that goal for the
    newly-added thin symbols.~~

    **RESOLVED (M7.5 Phase 0).** `run_m5_backtest` now precomputes per-symbol
    `ffill().shift(1)` "previous" series for the dip-arm RRS/LRSI crossings, so the
    comparison uses the symbol's own last real native reading across master-calendar
    gap rows. Regression test:
    `test_dip_arm_cross_uses_symbols_last_native_reading_across_a_gap_bar`.
24. ~~**`scripts/run_validation_studies.py`'s "shared baseline" doesn't fully eliminate the
    redundant precompute**~~ (found during the M7 study-suite's Task 6 review) -- the script calls
    `_prepare_m5` explicitly to get `baseline_prepared`, then calls `run_m5_backtest` with the same
    arguments right after, but `run_m5_backtest` has no parameter to accept an already-built
    `PreparedM5` and always recomputes one internally. So the baseline's expensive per-symbol
    precompute genuinely runs twice (once wasted) rather than once. Not a correctness bug -- just
    one extra ~15-20 minute run out of the suite's ~17 total. Fixing this cleanly needs a small,
    additive `run_m5_backtest(..., prepared: PreparedM5 | None = None)` parameter in
    `engine_m5.py`, out of the study-suite plan's scope; worth picking up in a future pass. --
    **RESOLVED (M7.5 Phase 0).** `run_m5_backtest` now accepts
    `prepared: PreparedM5 | None = None` and skips its internal `_prepare_m5` when
    given; `scripts/run_validation_studies.py`'s baseline passes its own
    `baseline_prepared`. The docstring lists which config fields are event-loop-only
    (safe to vary against a shared `prepared` — notably `stop_atr_mult`, making
    stop-multiplier sweeps nearly free) versus prepare-baked (need a fresh
    `_prepare_m5`).
25. **The M5 RRS sensitivity sweep (08 §3.3) found a real, actionable miscalibration candidate**:
    `rrs_m5_window=18` produced 10 real trades (vs. the spec-default `window=12`'s 3, and
    `window=6`'s 0) against the same 130-symbol universe. Not acted on in this milestone (same
    disclosed-but-deprioritized treatment as the D1 window finding, item #6 above), but a strong,
    numbers-backed candidate for a future recalibration pass -- see the M7 study-suite section
    below for the full sweep table.
26. **The bias-engine confusion matrix (08 §3.4, a genuinely new study, first run this milestone)
    found the bucketed bias call shows approximately zero directional skill above base rate** at a
    12-bar (~1 hour) forward horizon: `BULL`'s 33.9% hit rate for a subsequent UP move is
    statistically indistinguishable from the unconditional 34.9% UP base rate across all 97,231
    classified M5 bars; `BEAR`'s 35.1% is barely above the 30.3% DOWN base rate. One design point
    (horizon/threshold), not an exhaustive sweep, and the bias engine's role is gating direction
    rather than forecasting SPY in isolation -- but a real, honest finding worth further
    investigation (e.g. a horizon sweep) before relying on the bias engine's bucket as a
    standalone directional signal.
27. ~~`run_m5_backtest`'s `disabled_gates={"bias"}` was a silent no-op~~ **RESOLVED (`commit
    7f224b6`)**, found during the M7 final whole-branch review. `bias_ok_long`/`bias_ok_short`
    never consulted `config.disabled_gates`, unlike the D1 engine's already-correct handling --
    meaning the gate-ablation study's `disable_bias` run was guaranteed identical to baseline by
    construction, not a real test of that lever. Significant because 100% of this milestone's real
    trades enter via the 04 §6 trigger-bypass path, which itself requires
    `bias_ok_long`/`bias_ok_short` -- a genuine bias-disable was exactly the lever most likely to
    matter. Fixed for both directions (bypasses the bucket check, the 2-bar hold, and the short
    side's regime exclusion); the ablation study was re-run against real data after the fix (see
    the M7 study-suite section below for the corrected result: disabling `bias` does unlock 1
    additional trade, matching `rrs`/`ha`/`sma`'s pattern).
28. **Survivorship-driven PIT coverage decay in the M9 universe scan, now with measured
    numbers** (see "M9: nightly universe scan" section's Task 9 Step 3): point-in-time
    reconstruction uses the CURRENT Alpaca asset list, so a symbol delisted before `as_of`
    is silently absent and a symbol listed after `as_of` has no bars to evaluate at all —
    both erode measured coverage the further back `as_of` goes. Measured at the calibrated
    thresholds: ~1y back (2025-07-02) still scans cleanly (970 passed, `fail_coverage` 885);
    ~2y back (2024-07-02) refuses at 79% coverage; ~4y back (2022-07-01) refuses at 60%
    coverage — all below the 0.80 floor. A deep-PIT study must consciously lower
    `ScanConfig.min_coverage_fraction` and accept the disclosed survivorship bias rather than
    treating a passing scan as a trustworthy historical universe arbitrarily far back.
29. **Onboarded symbols all share one "UNKNOWN" sector bucket** (no sector/industry data
    comes back from `AlpacaClient.fetch_assets`, unlike the curated universe's
    `reference_overrides.yaml`-sourced sectors) — the selection engine's `max_per_sector`
    diversification cap (default 2) will throttle onboarded names against each other on any
    tradeable-list rebuild that mixes them with the curated universe, exactly as if they were
    all one real sector. Not wired around; worth a real sector lookup if onboarding volume
    ever makes this bind in practice.
30. **`scan/nightly.py`'s maintenance pass (`_run_maintenance`) re-issues a backfill call
    for every `insufficient_history` onboarded symbol on every single night**, unconditionally
    (not just once maturation is plausible) — cheap per call (the manifest makes an
    already-done unit a no-op) but unbounded in count as the onboarded set grows, and its
    maturation granularity inherits the underlying manifest's calendar-YEAR staleness (a
    symbol's daily-bar count can only advance when a fresh year-unit backfill actually runs),
    so a symbol can sit at `insufficient_history` for up to a year longer than its true bar
    count would justify. Not a correctness bug, just a coarser maturation clock than "the
    symbol crossed 300 daily bars" might suggest.
31. **Scan listing/liquidity heuristics carry the same disclosed substitutions as the spec**
    (see `scan/__init__.py` and `scan/config.py`'s module docstrings, restated here for the
    known-limitations list): no security-type field means ETF exclusion is a name/exchange
    heuristic (case-by-case `symbol_denylist` patches for stragglers like QQQ); no float data
    means the spec's float>=50M gate (01 §4.4) is substituted by the dollar-volume floor; and
    the halt-history gate (01 §4.5) is dropped entirely (no historical halt feed available).
    All three are pre-existing, deliberate v1 scope cuts, not new findings from Task 9's
    calibration — restated here because the known-limitations list is where a future reader
    checks first.
32. **The `screener_snapshots` archive is forward-only starting 2026-07-06.** The first
    capture attempt (2026-07-05, before the backdated-run guard existed) saved Sunday's
    real-time screener payload mislabeled under scan_date 2026-07-02; those 3 rows were
    deleted to keep the archive honest (`captured_at` preserved as evidence — see the M9
    section's "data hygiene note"). No screener data exists for any date before 2026-07-06;
    this is a real gap, not a bug, and won't backfill itself since the screener endpoints are
    real-time-only by design.
33. **`config/universe_500.yaml`'s top-up sector labels use Nasdaq's screener vocabulary
    ("Finance", "Health Care"), which differs from the curated universe's
    `reference_overrides.yaml` labels ("Financials", etc.)** — near-synonym labels that mean
    the same sector economically split `max_per_sector`'s diversification buckets across the
    curated/top-up boundary instead of merging them, a mild loosening of the intended
    diversification cap. Not fixed with a label-mapping table; disclosed instead (M10).
34. **The 372 M10 top-up symbols have no earnings-blackout data** —
    `reference_overrides.yaml`'s earnings blackout lists (already unpopulated for the
    curated 128, limitation #3 above) obviously don't cover symbols added after the fact.
    Any M10 campaign trade around a top-up symbol's earnings date is not protected by the
    blackout gate the spec calls for.
35. **M10 cohort campaigns are not a literal 500-symbol portfolio simulation.** Splitting
    into 4 cohorts of ~125 symbols each (`backtest/campaign.py::split_cohorts`) means
    portfolio-level constraints — max-concurrent positions, daily loss limits,
    consecutive-stop-out halts — apply independently *per cohort*, not once across the full
    universe, and each symbol's selection-engine ranking competition is against its ~124
    cohort-mates, not all 499 other symbols. Right question for signal-quality/sample-size
    (does this lever still work with more names in the mix), wrong question for "what would
    a single real 500-symbol book have done."
36. **The M7-style validation-study suite has not been retrofitted for `universe_file`
    overrides.** `scripts/run_validation_studies.py` hardcodes the curated-universe load
    path with no way to point it at `config/universe_500.yaml`; a plan-stage assumption that
    it already supported an override was wrong. Re-running the full ablation/walk-away/RRS-
    sensitivity/bias-confusion/time-of-day suite at 500 symbols (the natural follow-up to
    this milestone's headline window-promotion reversal) is deferred pending that retrofit.

## M7 pre-work: ADV-gate fix + full-universe audit (completed)

Before starting the studies suite, M7 first did the two prerequisite steps this file's prior
"Next: M7" section called for -- fixing the known-limitation #16 ADV-gate cadence bug and
building a committed, full-universe version of the M6 diagnostic -- to find out whether M6's
0-trade result was fixable with a real bug fix, or was purely a confluence-rarity finding. Both
were done via the same implementer + task-reviewer workflow used throughout this project.

**Fix: `gates.py::gate_adv` M5-cadence mismatch** (commits `ad2ee2b`, `b2b6eaa`, review clean,
first pass). `gate_adv` gained an optional `adv: pd.Series | None` parameter that, when given, is
compared directly against the threshold instead of recomputing `df["volume"].rolling(20).mean()`
-- the fix that was needed, since that rolling mean is a genuine 20-day ADV when `df` is D1 bars
(the D1 walking skeleton's usage, left untouched by the `None` default) but only a ~100-minute
average of 5-minute-bar volume when `df` is M5 bars. `adv20` is threaded through
`gates_pass_long`/`gates_pass_short`/`gates_pass_long_m5`/`gates_pass_short_m5`, and
`backtest/engine_m5.py`'s `_prepare_m5` now passes its own already-computed `adv20_native` series
into both M5 gate calls. The reviewer independently bug-injected the pre-fix code (confirms the
new tests fail without the fix) and a "half-fix" that accepts-but-ignores the `adv` parameter
(confirms the tests aren't vacuous). 185/185 passing after this fix.

**New tool: `backtest/studies/gate_audit_m5.py` + `scripts/audit_gate_pass_rates.py`** (commits
`5dc75b0`, `05363ce`, review clean, first pass). Extends the M6 diagnostic
(`.superpowers/sdd/task9_diagnostic.py`, a 7-symbol scratch script, never committed) into a
committed, reusable tool: `symbol_gate_rates` (per-gate + joint pass rates, long and short, on a
symbol's native M5 index) and `symbol_watchlist_reach` (independent per-symbol watchlist-state-machine
simulation), run across the full universe by `run_gate_pass_audit`, with a thin typer CLI writing
CSVs to `reports/gate_audit/`. The reviewer hand-recomputed the gate-rate test fixture's
percentages against `gates.py`'s real thresholds, hand-traced the watchlist-state test bar-by-bar
(including manually deriving `score_long_m5`'s constant value from `scoring.py`'s formula to
confirm the test's premise), and bug-injected a long/short gate function swap to confirm the
tests would catch it. 190/190 passing after this tool.

**Real result after the fix: 0 -> 3 trades.** Rerunning `scripts/run_backtest_intraday.py`
against the same 1252-day/128-symbol window with the ADV-gate fix applied now produces **3 LONG
trades** (shorts still disabled by default): STZ (2024-01-09, hard stop, -$7.88), ORCL
(2025-06-12, profit-take, +$149.04), PNC (2026-06-10, hard stop, -$24.33) -- win rate 33%, profit
factor 4.63, total PnL +$116.82 on $100k starting equity. The bug was real and worth fixing, but
3 trades over 5 years/128 symbols is nowhere near enough to run any of 08 §3's validation studies
meaningfully -- confluence rarity, not this bug, remains the dominant story.

**Full-universe audit confirms the 7-symbol finding generalizes, and finds something new.**
Running `scripts/audit_gate_pass_rates.py` across all 128 symbols (`reports/gate_audit/`):

- Joint gate-pass rate: long mean 0.0145% / median 0.0093% / max 0.0737%; short mean 0.0201% /
  median 0.0128% / max 0.1125% -- matches the M6 section's 7-symbol sample (0.00-0.02%) closely,
  confirming the finding generalizes across the full universe, the same "expand and reconfirm"
  step M3.5 took at D1 cadence.
- Per-gate breakdown across the universe (mean pass rate) identifies the tightest individual
  gates: `rrs_m5` 6.3% (tightest by far), `rrs_d1` 18.4%, `ha_cont_d1` 20.3%, `sma_stack` 36.9%,
  `not_one_candle_wonder` 32.7%, `volume_ratio_d1` 41.0%, `headroom` 50.2%, `vwap` 51.0%; `adv`
  now averages a healthy 94.0% post-fix (was the M6 section's wildly inconsistent 0.00-56.21%
  before), `price` 99.7%, `no_gap_exclusion` 99.9% -- confirms G1 (ADV) is no longer a
  contributor to the bottleneck, and that `rrs_m5`/`rrs_d1`/`ha_cont_d1` are the three tightest
  hard gates worth the closest look in any future ablation/recalibration study.
- Watchlist-state reach: 86/128 symbols (long) and 100/128 (short) reach `QUALIFIED` at least
  once, but **0/128 symbols ever reach `DIP_ARMED` on the long side** (1/128 short -- MSFT, 12
  bars `QUALIFIED`, 1 bar `DIP_ARMED`, 1 bar `ENTRY_EVAL`, over the full 5-year window).
- **This audit's 0-long-`DIP_ARMED` finding directly contradicts the 3 real trades existing --
  investigated, and it's not a bug.** All 3 real trades' entry timestamps land exactly one M5 bar
  after a `LONG_TRIGGER` fire in `bias/engine.py`'s trigger series (confirmed directly: computed
  `bias_series` for SPY/QQQ and checked the bar immediately preceding each of the 3 real entries
  -- all three show `LONG_TRIGGER`, matching `broker_sim.py`'s next-bar-fill convention). That
  means **100% of realized trades entered via 04 §6's trigger-bypass exception**
  (`watchlist.apply_trigger_bypass`, called by `run_m5_backtest`'s event loop) -- a `QUALIFIED`
  symbol jumping straight to `ENTRY_EVAL` on a matching bias-engine trigger bar, never through the
  normal "own dip" `DIP_ARMED` path this audit's `symbol_watchlist_reach` simulates. The audit
  tool doesn't call `apply_trigger_bypass` at all, so its `DIP_ARMED`/`ENTRY_EVAL` counts
  *undercount* real reachability on this axis (opposite of the cross-symbol-ranking omission,
  which *overcounts* -- see the module's docstring, corrected in commit `2515f78` to state both
  directions precisely instead of the original one-sided "upper bound" claim). Practically: the
  real system's only working entry path across the whole universe over 5 years was the
  market-wide trigger mechanism, not a symbol's own dip-arming -- a materially different, more
  specific finding than "confluence is rare" alone.

## Decision on the 3-trade sample: proceed now (resolved)

Three options were on the table before building the 08 §3 study suite: loosen specific gate
thresholds, expand the universe further, or proceed on the honest 3-trade sample now (matching
M3.5's own precedent of running its D1 studies on a small 8-trade sample and reporting results as
directional, not statistical proof). **The user chose to proceed now.** The full suite below was
built and run against the real 3-trade sample; loosening gates or expanding the universe remain
open options for a future pass, informed by this run's real findings (particularly the RRS window
sensitivity result below, which is now a concrete, numbers-backed candidate for a future
recalibration pass rather than a hypothesis).

## M7: full validation study suite (08 §3), M5 cadence -- built and run

Built via the same subagent-driven-development workflow as every prior milestone: 6 tasks (1
config-knob prerequisite + 5 study modules/CLI), each with a fresh implementer and independent
task reviewer, one fix-and-re-review round out of 6, plus one more fix-and-re-review round from
the final whole-branch review's `disabled_gates={"bias"}` finding. Plan:
`docs/superpowers/plans/2026-07-03-m7-validation-study-suite.md`. Test suite: 190 (M7 pre-work
checkpoint) -> 204.

**What was built:**
- `BacktestConfigM5.rrs_m5_threshold_long/short` + `rrs_d1_threshold_long/short` (commits
  `491bdc0`/`f14afd6`) -- new config knobs threaded through `_prepare_m5`'s gate calls, needed by
  the RRS sensitivity sweep below. The first-pass tests were found vacuous by the reviewer (passed
  even with the threading fully reverted, due to a fixture quirk) and replaced with
  `mock.patch(wraps=...)` tests spying on the real kwargs passed to the gate functions --
  independently re-verified via bug-injection by the re-reviewer.
- `backtest/studies/ablation_m5.py` (commit `87e1be5`) -- 08 §3.1's rule-count ablation, extended
  to M5's fuller 6-hard-rule set (bias, rrs, ha, sma, rrs_m5, vwap vs. D1's 4), reported separately
  for LONG and SHORT.
- `backtest/studies/walk_away_m5.py` (commit `e876802`) -- 08 §3.2's walk-away analysis (MFE/MAE
  of "IDLE -> QUALIFIED" signals vs. realized trade R), M5 cadence, both directions. Designed to
  take an already-computed `PreparedM5` + trades DataFrame as input (not raw universe dicts +
  its own backtest run), specifically to let the CLI share one baseline run across studies rather
  than repeating the ~15-20 minute precompute redundantly.
- `backtest/studies/rrs_sensitivity_m5.py` (commit `ea2c5ac`) -- 08 §3.3's RRS window x threshold
  sweep, M5 cadence: `rrs_m5_window` in {6, 12, 18} (algo-spec 02/04's own L sweep) x threshold in
  {0.75, 1.0, 1.5}, 9 combinations, overall + per-direction metrics.
- `backtest/studies/bias_confusion_m5.py` + `backtest/studies/time_of_day_m5.py` (commit
  `4b27f9e`) -- two genuinely new studies (08 §3.4/§3.5, never built at any cadence before now):
  bias-bucket-vs-forward-price-direction confusion matrix, and time-of-day/regime trade slicing.
- `scripts/run_validation_studies.py` (commit `8cb9ea2`) -- the CLI wiring all 5 studies together,
  computing one shared baseline `_prepare_m5`/`run_m5_backtest` run and threading it into
  ablation/walk-away/time-of-day (only RRS sensitivity's 9 combinations and ablation's 6
  gate-disabled variants need genuinely fresh runs). The final whole-branch review found this
  "shared baseline" doesn't fully eliminate the redundant precompute: `run_m5_backtest` has no
  parameter to accept a pre-built `PreparedM5`, so it silently recomputes one internally right
  after the script's own explicit call -- a real, non-blocking inefficiency (one extra ~15-20
  minute run out of ~17 total), inherited from `engine_m5.py`'s existing public API and out of
  this plan's scope to fix. Worth a small `run_m5_backtest(..., prepared=None)` optional-parameter
  addition in a future pass.

**Real bugs found and fixed:** the 6 build tasks themselves needed only 1 fix-and-re-review round
(a vacuous test, not production code -- the production code in that task was approved first
pass). But the **final whole-branch review** (opus, range `7aeb333..6304f78`, 14 commits) found a
real, non-trivial production bug: `run_m5_backtest`'s `bias_ok_long`/`bias_ok_short` computation
never consulted `config.disabled_gates` at all, unlike the D1 engine's already-correct handling --
making the ablation study's `disable_bias` run a **guaranteed no-op duplicate of baseline by
construction**, not a real test of that lever. This mattered specifically because 100% of this
milestone's real trades enter via the 04 §6 trigger-bypass mechanism, which itself requires
`bias_ok_long`/`bias_ok_short` to be true -- making a genuine bias-disable the single most likely
lever to change the real trade count, not a harmless inert rule like `vwap` turned out to be.
Fixed (commit `7f224b6`, reviewed clean, first pass): `bias_ok_long`/`bias_ok_short` now bypass the
whole bias-family-plus-hysteresis-plus-regime-exclusion check when `"bias"` is in
`disabled_gates`, for both directions (D1 only needed LONG, since D1's own ablation study is
long-only; M5's is bidirectional). Purely additive -- every caller not setting that flag sees
identical behavior. The reviewer hand-traced both new regression tests' watchlist-state
transitions and independently reproduced the bug-injection (tests fail pre-fix with an empty
trade log, pass post-fix). Only the ablation study's `disable_bias` variant was affected by this
bug -- nothing else in the suite touches `disabled_gates` -- so only that one study was re-run
against real data after the fix (not the full 3-hour suite); its section below reflects the
corrected result.

**Real run**, `python scripts/run_validation_studies.py`, full 130-symbol/5-year warehouse
(`shorts_enabled=True` throughout, so both books actually traded), user-run directly (not
backgrounded by the agent) for full visibility, ~3 hours wall clock (ablation section below
reflects a corrected, agent-run re-run of just that one study after the bias fix, ~1.5 hours):

- **3.1 Gate ablation** (re-run after fixing the `disabled_gates={"bias"}` no-op bug documented
  just above; baseline 3 trades). Disabling `bias`, `rrs`, `ha`, or `sma` individually each
  unlocked exactly 1 additional distinct trade (a different symbol each
  time -- `AMD` 2023-03-16 via `bias`, `ORCL` 2023-06-14 via `rrs`, `KLAC` 2026-06-10 via `ha`,
  `AMGN` 2025-02-03 via `sma`); disabling `rrs_m5` or `vwap` unlocked nothing. 7 total distinct
  trades scored across all 7 runs, split 3 at rule_count=5 (missing exactly one of the 6 rules)
  and 4 at rule_count=6 (all rules satisfied) -- too small a sample for
  win-rate/expectancy-by-rule-count to be meaningful (M3.5's own D1 version found zero rules ever
  unlocked a trade; the fuller M5 rule set is at least slightly more informative than that, but
  still far short of a real signal). No SHORT trades occurred in any ablation run. Note: the
  per-trade `bias_ok` column in `ablation_trades.csv` reflects only the raw bias-bucket check at
  the signal bar (matching `_rule_ok_long`'s classification logic), not the real event loop's
  stricter 2-consecutive-bar-hold entry requirement (`engine_m5.py`'s `bias_ok_long`) -- so `AMD`'s
  scored row shows `bias_ok=True` even though disabling `bias` was what let it actually enter (its
  signal bar's bucket alone qualified, but the bar before it didn't, so the real 2-bar-hold gate
  blocked it until the whole check was bypassed). Not a bug in the scoring, just a real distinction
  between the classification yardstick and the engine's actual (stricter) gating logic, worth
  knowing before reading `rule_count` as identical to "would enter without this rule."
- **3.2 Walk-away analysis**: 747 real entry signals (`IDLE -> QUALIFIED`, both directions) --
  vastly more than the 6 that ever became a scored trade above, confirming the qualification step
  itself is not the bottleneck; the bottleneck is what happens after qualification (dip-arming /
  the trigger-bypass path, matching the M7 pre-work's finding that the "own dip" path essentially
  never fires). MFE/MAE both directions show large, roughly symmetric two-sided distributions
  (LONG: mean MFE 4.61R / mean MAE -5.48R; SHORT: mean MFE 5.85R / mean MAE -6.33R) -- these
  RS/RW-qualified names move a lot in both directions with no active management, confirming (as
  M3.5 found at D1, mean MFE ~1.9R vs. realized ~-0.11R) that active risk management materially
  changes the outcome: the 3 realized baseline trades' R (mean -0.28, median -0.86) are far more
  contained than the walk-away distribution's tails in either direction, at the cost of also
  capping the occasional large favorable excursion.
- **3.3 RRS sensitivity sweep -- the single most actionable finding of this run.** `window=6`
  produces 0 trades at every threshold; `window=12` (the spec's own L default, same as the
  baseline run) produces 3; `window=18` produces **10 trades at threshold 0.75/1.0** (9 LONG, 1
  SHORT) and 5 at threshold 1.5 -- more than 3x the baseline trade count from widening the RRS_M5
  rolling window alone. This is the opposite direction of M3.5's D1 finding (`window=3`, narrower
  than the D1 default of 5, outperformed) -- at M5 cadence a WIDER window appears to help, not a
  narrower one. Not acted on in this milestone (matching the disclosed-but-deprioritized treatment
  of the D1 finding, known limitation #6) but now a concrete, numbers-backed candidate for a
  future recalibration pass, more actionable than the D1 finding ever was given the trade-count
  multiplier involved.
- **3.4 Bias-engine confusion matrix -- a real, slightly concerning null result.** Hit rates:
  `STRONG_BULL` 34.9% predicting UP, `BULL` 33.9%, `BEAR` 35.1% predicting DOWN, `STRONG_BEAR`
  24.5% (n=49, noise), `NEUTRAL` 32.9% predicting FLAT. The overall marginal base rates across all
  97,231 classified bars are UP 34.9% / FLAT 34.8% / DOWN 30.3% -- meaning **`BULL`'s hit rate is
  statistically indistinguishable from just always guessing UP with no bias engine at all**, and
  `BEAR`'s hit rate is barely above the DOWN base rate. This is one design point (12-bar / ~1-hour
  forward horizon, 0.1% flat threshold) not an exhaustive sweep, and doesn't by itself invalidate
  the bias engine (which exists to gate trade direction, not to forecast SPY in isolation), but is
  a real, honest finding worth flagging: at this horizon, the bucketed bias call shows
  approximately zero directional skill above base rate.
- **3.5 Time-of-day / regime slicing**: only 3 trades total (the baseline run's), all LONG, all in
  the MIDDAY/TREND_UP bucket -- correctly, honestly thin given the sample size; not informative on
  its own, included for completeness and ready to become useful once a larger trade count exists
  (e.g. via the RRS window=18 finding above).

**Interpretation.** M7's necessary-first-step (get M6 to produce a real trade log) succeeded: 0 ->
3 trades from the ADV-gate fix, and this run's RRS window=18 sweep result shows the ceiling is
much higher (10 trades) without touching any hard gate threshold at all -- confluence rarity is
real but not as immovable as the 0-trade result first suggested. The bias-engine confusion-matrix
null result and the ablation's still-small sample both argue for a follow-up pass (either the RRS
window recalibration or a universe expansion, or both) before treating any of these directional
findings as validated. None of this milestone's numbers should be read as "the system works" or
"the system doesn't work" -- exactly the caveat M3.5 applied to its own 8-trade D1 findings.

## M7.5 Phase 0: tuning-campaign enablers -- built, reviewed, run (2026-07-04)

The M7 review session produced a tuning campaign plan (`docs/tuning/m7.5-tuning-matrix.md`,
results ledger `docs/tuning/ledger.csv`) built on three review findings: the dip-arm path is
structurally unreachable as implemented (the QUALIFIED-hold requires `rrs_m5 >= 1` on the same
series whose zero-cross is supposed to arm the dip -- a logical contradiction), stops at
1.0xATR(M5) sit inside single-bar noise (2 of 3 real trades stopped out inside their own entry
bar, 0.05-0.10% stop distances), and the trigger bypass -- the only working entry path -- is
throttled by preconditions anti-correlated with fresh triggers. Phase 0 built the five campaign
enablers (plan: `docs/superpowers/plans/2026-07-04-phase0-tuning-enablers.md`, commits
`ca88df1..d2e6879`, 5 first-pass task approvals + clean final whole-branch review, test suite
204 -> 215):

1. **`BacktestConfigM5.stop_atr_mult`** (default 1.0, never silently clamped) threaded into both
   books' `risk.stop_price_long/short` calls -- Round 3's stop sweep is a config field now.
2. **Known limitation #23 RESOLVED**: dip-arm RRS/LRSI cross detection now reads each symbol's
   last real native value via precomputed `ffill().shift(1)` series instead of the reindexed
   frame's previous master-calendar row (NaN on gap bars, silently suppressing crossings for
   thin symbols).
3. **Entry-funnel instrumentation**: 36 flat counters through `run_m5_backtest`'s event loop
   (qualifications, dip-arms, trigger coincidences and their kill causes, submission-stage kill
   causes, orders/fills/cancels), returned as `BacktestResultM5.funnel` and written to
   `reports/m5_backtest/funnel.json` by the CLI with `same_bar_stop_rate`. Verified
   behavior-preserving (counters only; the final review re-derived control-flow equivalence
   against the pre-branch code line-by-line).
4. **Known limitation #24 RESOLVED**: `run_m5_backtest(..., prepared: PreparedM5 | None = None)`
   skips the ~15-20 min precompute; docstring documents exactly which config fields are
   event-loop-only (safe to vary against a shared `prepared` -- notably `stop_atr_mult`) vs.
   prepare-baked. `scripts/run_validation_studies.py`'s baseline now passes its own
   `baseline_prepared`.
5. **Trigger forward-return study** (08-style, new): `backtest/studies/trigger_skill_m5.py` +
   `scripts/run_trigger_skill_study.py` (SPY/QQQ only, ~1 min, no backtest run) -- the analog of
   the M7 bias-bucket confusion matrix for the *trigger*, the signal that actually gates 100% of
   real entries. Returns measured from the fire bar's close (signal skill, not achievable PnL).

**Real results from the post-build runs:**

- **Behavior preservation confirmed on real data**: the default-config full-warehouse re-run
  reproduces M7's exact 3 trades (STZ/ORCL/PNC, PF 4.63, PnL +$116.82).
- **Trigger skill** (`reports/tuning/trigger_skill.csv`, n=1,591 LONG / 561 SHORT fires over 5
  years): LONG_TRIGGER's mean forward SPY return is 2-3x the all-bars base rate at 12/24-bar
  horizons (0.023%/0.040% vs 0.008%/0.017%), hit-rate edge modest (44.4% vs 41.7% UP at 24
  bars). SHORT_TRIGGER shows no skill -- mean forward return stays *positive* after it fires
  (consistent with the window's bull drift). Modest, real, honest: the long trigger carries
  some timing signal; the short trigger does not.
- **The first measured entry funnel** (long book, default config, 5 years): 346 qualification
  signals -> 15 trigger-bar x QUALIFIED coincidences -> 5 killed by the bias 2-bar hold -> 10
  bypasses -> 7 killed by `confirm_trigger_entry_long`'s quality check -> 3 submitted -> 3
  filled. `dip_armed = 0` (the Path B dead-end is now measured, not inferred). Zero
  opportunities lost to entry window, risk halts, ranking, slots, sizing, or unfilled orders.
  New finding: the confirm-trigger quality check is the single biggest post-coincidence
  throttle (7 of 10) -- bigger than the bias hold -- and wasn't on the original lever list;
  recorded in the matrix as lever candidate A4.

**Deferred, disclosed** (final review's triage, all counter-semantics refinements to revisit
once real runs show which counters carry weight): a funnel blind spot (ENTRY_EVAL symbol-bars
skipped because the symbol is already in positions/pending are counted nowhere),
`eval_killed_by_ranking` conflates cross-sectional ranking cuts with the `min_list_score`
floor, the short book's `stop_atr_mult` threading is verified by review but not by a dedicated
test, `same_bar_stop_rate` counts any same-bar exit (not just hard stops), and the CLI's
funnel echo hides zero-valued counters (funnel.json has them all).

**Next**: tuning Rounds 1-4 per `docs/tuning/m7.5-tuning-matrix.md` §3 -- Round 1 (dip-arm
alert-model redesign, the structural unlock) is the first real experiment; every run gets a
row in `docs/tuning/ledger.csv`.

## M7.5 R1+R4: campaign levers built + experiment rounds run (2026-07-04)

Second M7.5 build milestone (plan `docs/superpowers/plans/2026-07-04-m7.5-round1-round4.md`,
commits `16968b8..26be6ac`, 5 tasks all approved first-pass, final whole-branch review's one
Important finding -- a funnel-partition leak in the new dip-hold modes -- fixed and
independently verified; test suite 215 -> 228). Two deliberate behavior changes: (1)
`trail_stop` exit label split from `hard_stop` (a stop exit after the 1.5xATR trail trigger
armed is a managed exit, and no longer feeds the same-day lockout or the consecutive-stop-out
halt -- the spec's stop-OUT semantics); (2) `BacktestConfigM5.rrs_m5_window` default promoted
12 -> 18 per the Rounds 2-3 sweep. New knobs: `bias_hold_bars` (2 = old behavior exactly),
`confirm_not_extended_atr_mult` + config RRS thresholds now reaching the (previously
hardcoded-at-±1.0) `confirm_trigger_entry_*` recheck, and `dip_hold_mode`
{strict, d1_session, grace} + `dip_hold_grace_bars` -- the Round 1 "alert model" that lets a
QUALIFIED symbol survive its own dip (hold gate = gates minus {rrs_m5, vwap,
one_candle_wonder}; session reset in d1_session; strict is bit-for-bit the old behavior).
Funnel gained `*_trigger_killed_by_gate` so the coincidence partition is exhaustive in every
mode. Driver gained `--config-json`/`--run-tag`.

**Experiment results** (user-run manually after background jobs were repeatedly killed on
this machine; all rows in `docs/tuning/ledger.csv`, analysis in
`docs/tuning/m7.5-tuning-matrix.md` "Rounds 1+4+5" section): `bias_hold_bars=1` is the
campaign's best single lever -- strictly additive, 13 trades / PF 3.71 / +$753 vs the
10 / 2.06 / +$262 anchor, capturing the exact AMD trade M7's ablation flagged -- PROMOTED
candidate pending a robustness pass. The alert model works mechanically (342 dip-arms vs 4
under strict) but Path B has still never converted: ~200 die at the doctrine-level bias veto
(dips recover while SPY is still not bullish), ~90 at `dip_quality_pass_long`, whose
VWAP-held + shallow-depth constants structurally contradict an RRS-zero-cross dip (new lever
candidate A5: parameterize them). Threshold loosening, retested with the confirm fix live,
is definitively rejected (PF 2.06 -> 1.41 -> 1.02 at 1.0/0.5/0.0): the edge is strong-RRS
names entered at fresh bias flips. The r5 combo (d1_session + bias_hold_bars=1) equals r4a
to the cent.

## M7.5 study-suite re-run on the promoted baseline (2026-07-04/05)

`scripts/run_validation_studies.py` re-run (user-run, ~90 min) against the promoted config
defaults (rrs_m5_window=18, bias_hold_bars=1; commit `8643242`). Baseline reproduces the
r4a promotion run exactly (13 trades, +$752.57). Full outputs in `reports/m7_studies/`
(overwriting the M7 originals, which live on in git history and the M7 sections above).

- **3.1 Gate ablation -- the spec's monotonicity hypothesis is CONFIRMED for the first
  time.** Rule-count buckets are finally populated (30 long trades across buckets vs 7 at
  M7): trades satisfying all 6 hard rules win 58.3% with +0.20 avg R (expectancy +$65.6);
  trades unlocked by disabling exactly one rule win 33.3% with -0.39 avg R (+$13.2). More
  rules = better trades, exactly what 08 §3.1 was designed to test and could never show on
  degenerate samples (M3.5: all trades in one bucket; M7: 7 trades). Notable detail:
  disable-ha unlocked the single biggest trade in the study (IBM 2024-07-25, +$524) yet its
  bucket is still net-worse -- the rule earns its keep on average, not on every trade.
  Shorts remain weak in every bucket (5 of 6 short trades lost).
- **3.3 RRS sensitivity**: peak confirmed at window=18 / threshold=1.0 (13 trades, PF 3.71,
  +$753). threshold=0.75 is a legitimate volume-over-quality alternative (19 trades, PF
  2.73, +$756 -- same total PnL, more trades, lower PF); window=6 still produces zero
  trades; window=12 rides almost entirely on AMD (PF 18.5 on 4 trades -- small-sample
  artifact). With the confirm-fix live the threshold dimension is real, and 1.0 still wins
  on quality.
- **3.5 Time-of-day/regime -- first readable slice**: MIDDAY/CHOP longs are the sweet spot
  (4 trades, 4 wins, +$605 of the +$753 total); MIDDAY/TREND_UP longs are mediocre (5
  trades, 40% win, +$134). Consistent with the split-half finding (edge concentrated in the
  2022-23 chop) and with the source doctrine's own "chop is an excellent environment for
  day trading." Directional only at 13 trades, but now pointing somewhere specific.
- **3.2 Walk-away**: 984 qualification signals (up from 747 -- w18 default qualifies more),
  MFE/MAE still large and symmetric (LONG +5.3R/-5.7R, SHORT +6.1R/-6.9R): the raw signal
  still carries no unmanaged directional edge; selection + timing + management is where the
  realized PF 3.71 comes from.
- **3.4 Bias confusion matrix**: identical to M7 (same engine inputs) -- doubles as a
  sanity check that the suite ran cleanly.

Campaign position after this: the promoted baseline (w18, hold=1, strict, 1.0xATR stops) is
now validated by the full study suite, with the ablation finally supporting the confluence
philosophy the system is built on. Open threads, in rough priority: shorts look net-negative
everywhere (consider shorts_enabled=False for the headline config, or a short-side
recalibration); lever A5 (dip-quality parameterization) if Path B is pursued further;
universe expansion as the remaining sample-size multiplier.

## M8: backtest UI

A different axis of work than the M4-M7.5 trading-system milestones and M9's discovery
half: a **presentation layer** over stores that already existed (the M6/M7.5 Postgres
runs-store, plus M9's scan tables once those landed) — no new indicator, gate, or backtest
behavior. Built as 8 tasks (7 implementer tasks each with its own fresh-subagent review,
plus this closing docs+smoke task); spec/plan under `docs/superpowers/`. 329 unit tests
(`tests/unit/test_ui_pages.py` + `test_ui_data.py` + `test_ui_form.py`, roughly), all
`AppTest`-driven with `rs_spy.ui.data` monkeypatched — hermetic, no real Postgres needed
to keep the main suite fast.

**What was built** — `app.py` (repo root) + `src/rs_spy/ui/`:

- `app.py` — `st.navigation` over 5 pages: Runs (`/runs`, default), Configure & Run
  (`/run`), Compare (`/compare`), Scan & discovery (`/scan`), Campaigns (`/campaigns`).
- `ui/data.py` — the only module that touches Postgres/`store/*` or launches jobs; every
  page function calls `data.<fn>(...)` as a module attribute (never `from ui.data import
  fn`) specifically so tests can monkeypatch it and never need a real database. Wraps
  `store/repository.py` (`runs_df`, `run_detail`, `trades_df`, `equity_series`,
  `config_of`) and `store/scan_repository.py` (`scan_dates`, `passing_history`,
  `scan_funnel`, `universe_snapshot`, `onboarded_df`) for the M9 scan tables, plus
  `campaign_groups`/`parse_campaign_label` (regex `^m10-(.+)-([A-Za-z0-9_]+)-c(\d+)$`,
  matching M10's campaign label convention — a cross-milestone contract this task's
  self-review explicitly checked).
- `ui/form.py` — a pure (no `streamlit` import), unit-testable dataclass introspector:
  `field_specs` walks `dataclasses.fields(BacktestConfigM5)` and type-dispatches to a
  widget kind (`bool`/`int`/`float`/`choice`/`gates`/`symbols`/`str`), so the form
  automatically tracks any future `BacktestConfigM5` field instead of needing a
  hand-maintained list. `ADVANCED_FIELDS` (`extra_symbols`, `universe_file`,
  `trade_symbols_override`, `disabled_gates`) split into a collapsed expander.
- `ui/pages.py` — the render functions. Runs page: `st.fragment(run_every="5s")` wraps
  just the table (not the whole page) so the newest-first, status-badged, headline-metric
  list auto-refreshes without a full rerun stealing focus from anything else on the page;
  a `limit`/`show-more` session-state counter avoids ever loading unbounded history. Run
  detail: trades table, `st.line_chart` equity curve, metrics, gate-funnel counts, and the
  exact stored config in a JSON expander, plus a "clone into Configure & Run" button that
  seeds the form from `config_of(run_id)` (clone-and-tweak). Compare: 2+ succeeded runs,
  side-by-side metrics + equity curves rebased to 100 and overlaid on one `st.line_chart`.
  Campaigns: groups runs by the M10 label convention, and — cross-plan dependency, noted
  in this task's self-review — calls `backtest.aggregate.aggregate_campaign` (an M10
  artifact) to pool a variant's cohort runs, refusing (via `CampaignIncompleteError`) to
  render a partial campaign as if it were the full sample.
- **Job execution model**: `ui/data.create_and_launch` does `repo.create_run(status=
  'queued')` then `jobs/launch.py::launch_run` — a **detached subprocess**
  (`start_new_session=True`, parent does not `wait()`) running
  `scripts/run_backtest_job.py --run-id <uuid>`, which flips the row through
  running/succeeded/failed. The UI only ever polls Postgres; a backtest never runs
  in-thread inside the Streamlit process (a launched run outliving a closed browser tab or
  a Streamlit restart is the whole point, not a bug).

**Deliberately out of scope** (v1, all noted in the task 8 self-review as intentional cuts,
not oversights):

- **Real-time signals / live-trading view** — this UI only ever shows *backtest* runs
  pulled from Postgres after the fact; there is no live-market or paper-trading dashboard.
  Deferred to a future discovery milestone (tentatively "**discovery milestone #2**" —
  distinct from M9's discovery-of-universe work, this would be discovery-of-live-signal
  presentation).
- **D1 (daily-bar) backtests** — the UI's Configure & Run form only builds `BacktestConfigM5`
  (the M5/intraday engine); the D1 walking-skeleton engine (`backtest/engine.py`) has no UI
  path and is still CLI-only (`scripts/run_backtest_d1.py`).
- **Study-suite triggering** — the validation-study suite (ablation, walk-away, RRS
  sensitivity, bias confusion, time-of-day) has no Configure-&-Run-style form or launch
  button; it remains a CLI-only workflow (`scripts/run_validation_studies.py`).

**Stale-run caveat (v1, documented not automated)**: because a run is a detached
subprocess, a hard `SIGKILL`/OOM/host-crash can leave its row stuck at `status='running'`
forever — nothing flips it to `failed`. There is no automatic reaper in v1; `jobs/launch.py`
documents the query a human (or a future cron) can use to flag likely-dead runs:
```sql
SELECT * FROM runs
WHERE status='running' AND started_at < now() - interval '2 hours';
```
The UI itself shows whatever status is actually stored — it does not second-guess a
long-`running` row.

**Task 8 (this task): docs + live smoke.** Per an explicit controller deviation from the
original plan, **the run-launch smoke was deferred rather than run in this task**: a
long-running bulk backfill held the main DuckDB warehouse's write lock at the time, and a
launched job opens that warehouse read-only (`engine_m5.py`'s `_prepare_m5` path) — a run
launched under lock contention would fail for an environmental reason unrelated to the UI
code being validated, and would leave a misleading `failed` row in the real store. The
`create_run`+`launch_run` path is exercised by the (mocked) unit tests already, and will
get its real end-to-end exercise for free during the **M10 campaign execution**, which
drives the identical code path at much higher volume. What *was* run live: `streamlit run
app.py` against the real Postgres store (port 55432), all 5 page paths (`/runs`, `/run`,
`/compare`, `/scan`, `/campaigns`) verified to render with no exceptions — both an HTTP-200
check against the running server and, because Streamlit's initial HTTP response is a
static shell (the page script only executes over the app's websocket session), a
`streamlit.testing.v1.AppTest` run of each page function directly against the same live
store to actually execute the render code. Scan & discovery showed real M9 data: 14,021
assets evaluated, 1,450 passed (matches M9 Task 9's calibrated funnel exactly), one scan
date on record, zero onboarded symbols (consistent with M9 Step 4 — the first live
nightly/onboarding run — still pending). Runs/Compare/Campaigns rendered their empty
states cleanly (zero rows in `runs`, as expected since no run has been launched from this
UI yet). No UX bugs found; no code changes were needed as a result of the smoke.

## M9: nightly universe scan (discovery)

New milestone, a different half of the system than M4-M7.5: instead of trading a fixed
130-symbol curated universe, **discover what should be tradeable at all** (algo-spec 01 §4).
Spec: `docs/superpowers/specs/2026-07-05-universe-scan-design.md`; plan:
`docs/superpowers/plans/2026-07-05-m9-universe-scan.md`. Built as 9 tasks (8 implementer
tasks each with its own fresh-subagent review, commits `2e6a7e1..22275f0`, plus a final
whole-branch review + 4-commit fix round `47e6f56..c9b4534`; this doc's Task 9 closes the
milestone with real-data calibration).

**What was built** — a new `src/rs_spy/scan/` package:

- `scan/config.py` — `ScanConfig` (feed presets `iex`/`sip`, price/coverage/ADV thresholds,
  listing-heuristic allow/deny lists) + the gate order.
- `scan/engine.py` — `compute_scan_metrics` (as-of, causal-by-construction SQL: only bars
  dated `<= as_of` ever enter the window) + `apply_gates` (first-fail attribution over
  `GATE_ORDER = (listing, coverage, price, adv_shares, adv_dollars)`, so the funnel
  partitions every evaluated symbol exactly once) + `run_universe_scan`, the one code path
  used for both the live nightly scan (`as_of=today`) and point-in-time reconstruction
  (`as_of=`any cached date) — with a `ScanCoverageError` refusal when too few
  listing-eligible symbols have a bar for `as_of` (holiday/weekend/outage), rather than
  silently emitting a biased snapshot.
- `scan/bars.py` — a **separate DuckDB file** (`data/scan.duckdb`, `Settings.
  resolved_scan_warehouse_path`, same `bars`/`fetch_manifest` schema as the main warehouse,
  `warehouse.connect()` reused as-is) so ~14k-symbol broad-scan data never bleeds into
  curated-universe queries and the scan's nightly read-write connection never contends with
  concurrent read-only backtests on `warehouse.duckdb`. `refresh_daily_bars` combines the
  manifest's idempotent historical backfill with an unconditional, self-healing tail
  re-fetch (a calendar-year manifest unit is marked done at first fetch and goes stale as
  the current year grows; the tail start self-heals to the newest stored bar so a run after
  any outage catches up in one pass).
- `scan/onboarding.py` — most-active auto-onboarding: `select_onboarding_candidates` caps
  on the RAW top-N most-actives ranking *before* gate filtering (per spec — a night where
  the raw top-N is all ETFs/sub-$10 movers correctly yields zero candidates, not a
  filter-then-cap workaround), then `onboard_symbol` does a dual (daily year-chunk + minute
  month-chunk) backfill into the MAIN warehouse (that's where backtests read). A symbol
  with `< MIN_HISTORY_DAYS=300` daily bars is flagged `insufficient_history` (onboarded but
  excluded from launched runs); zero fetched bars in either cadence means the caller must
  not record the symbol at all (manifest retries it next night). `onboard_symbol` also
  tail-heals both cadences from the newest stored bar on every call (same pattern as
  `scan/bars.py`'s tail stage): the year/month manifest unit spanning `end` is marked `ok`
  at whatever partial boundary the first onboarding call used and `pending_symbols` never
  revisits an `ok` unit, so without this a symbol onboarded mid-year/mid-month would
  permanently miss bars for the rest of that period instead of healing on the next
  maintenance visit.
- `scan/nightly.py` — `run_nightly` orchestrates screener capture -> refresh+scan ->
  record -> onboard (new candidates + a maintenance pass) -> re-run, with every stage
  isolated so one symbol's failed onboarding or a failed screener capture never blocks the
  rest of the night (failures land in `NightlyReport.errors`, not raised) — except a scan
  `ScanCoverageError`, which does propagate, since no snapshot should exist for a refused
  night. Cron line (documented, not auto-installed; this machine runs America/Chicago):
  `0 16 * * 1-5  cd .../rs-spy && .venv/bin/python scripts/run_nightly_scan.py` (16:00 CT
  == 17:00 ET, RTH-only policy per spec).
- `data/alpaca_client.py` extensions: `fetch_assets` (all active US-equity assets,
  deduplicated by symbol; Alpaca has no security-type/float field, hence the listing
  heuristics) and `fetch_screener_snapshots` (most-actives by volume/trades + market
  movers — real-time-only endpoints, no `as_of` parameter, hence the nightly recorder).
  Both go through the existing `_request_with_retry` backoff policy.
- Postgres: 4 new tables (`scan_runs`, `universe_snapshots`, `screener_snapshots`,
  `onboarded_symbols`) + `store/scan_repository.py` (plain-SQL, mirrors `store/
  repository.py`'s style; `save_scan` deletes+`COPY`s each date's snapshot rows inside one
  transaction so a re-run of the same night converges rather than duplicating).
- `BacktestConfigM5.extra_symbols` (a new tuple field) + a small merge in `jobs/runner.py`
  (curated symbols plus any `extra_symbols` not already known) let a single tagged backtest
  run span curated + onboarded symbols without touching `universe.yaml`. A dedicated
  calendar-invariance test (`tests/unit/test_engine_m5_backtest.py`) locks down that the
  M5 engine's SPY-derived master calendar can never be truncated by a newly onboarded
  symbol's shorter history — the reason `insufficient_history` symbols are safely
  includable once they mature rather than needing to be filtered out of the run entirely.
  Maturation itself never triggers a tagged backtest run, though: a matured symbol only
  joins `extra_symbols` on the next night that already produces new onboarding candidates
  (deliberate — nightly runs aren't spammed solely to pick up a maturity-status flip).
- CLI: `scripts/run_nightly_scan.py` (`--as-of`, `--feed`, `--top`, `--no-onboard`,
  `--no-launch`).

**Final whole-branch review + fix round** (4 commits): (1) a **backdated-run guard** —
`fetch_screener_snapshots` has no `as_of` parameter (always "right now"), so saving its
payload under a past `scan_date` would silently overwrite that date's genuine archived
snapshot and poison onboarding with a past passing-set crossed with today's most-actives;
a backdated run (`as_of` != today ET) now skips screener-capture and onboarding entirely
and only re-runs the fully-re-runnable scan/PIT half; (2) **retry + screener-first
ordering** — `fetch_assets`/`fetch_screener_snapshots` now go through the shared retry
policy, and screener capture was moved to run *before* the 15-45 min refresh+scan so a
slow or failed refresh can never cost the day's irreplaceable, uncapturable-later screener
snapshot; (3) an **onboarding maintenance pass** (`nightly.py::_run_maintenance`) added to
repair two otherwise one-way ratchets: an `insufficient_history` symbol (e.g. a recent
IPO) is now re-evaluated nightly so it eventually matures past `MIN_HISTORY_DAYS` instead
of being excluded forever, and a partially-failed minute backfill (some month manifest
units landed `error`) is now retried via `data/manifest.py::symbols_with_error_units`
instead of leaving a permanent hole — both reuse `onboard_symbol`, which was already
resumable; (4) a minor batch (parquet-write failure isolated from the Postgres save that
already committed, most-actives dedup, docstring tightening).

### Task 9: real-data calibration (as-of 2026-07-02 — last trading day; Fri 07-03 was the
observed July-4th holiday)

**Step 1 — initial backfill + first scan.** The one-time broad daily backfill covered
**14,021 active US equities** in ~35 min (rate-limited); `scan.duckdb` landed at ≈0.97 GB.
The first scan, run before calibration at the plan's placeholder 30k-shares/$750k-dollars
IEX thresholds, passed **1,823/14,021**. Funnel: `fail_listing` 7,030, `fail_coverage` 101,
`fail_price` 2,682, `fail_adv_shares` 2,293, `fail_adv_dollars` 92. A convergence re-run
afterward completed in **32 seconds** (manifest no-ops + self-healing tail only) and landed
on **1,450/14,021** — matching the post-calibration count exactly, since the re-run picked
up the promoted thresholds. That re-run also exercised the backdated-run guard live end to
end: its report carried `"backdated run: screener+onboarding skipped (screener endpoints
are real-time-only)"`.

**Step 2 — IEX threshold calibration.** Sweeping shares/dollars against the real warehouse:
20k/500k -> 2,075; 30k/750k -> 1,823; 50k/1.25M -> 1,462; 75k/2M -> 1,121; 100k/3M -> 862.
50k/1.25M drops **EQIX** — a curated symbol (`last_close` ≈ $1,002, IEX `adv_shares` ≈ 44.4k
but `adv_dollars` ≈ $47.4M/day) — because a shares floor penalizes high-priced liquid names
regardless of their real dollar turnover; this is the same rationale behind substituting
the dollar-volume floor for the spec's unavailable float gate. A refined grid found
**40k shares / $2M dollars -> 1,450 passing with all 128/128 curated symbols passing**,
comfortably inside the spec's 800-1,500 sanity band. **Promoted**: `IEX_MIN_ADV_SHARES=
40,000`, `IEX_MIN_ADV_DOLLARS=2,000,000` (`scan/config.py`, this commit). Measured
universe-coverage fraction among listing-eligible symbols at 2026-07-02: **0.993** against
the 0.80 refusal floor — a comfortable safety margin for live nights, resolving the open
concern (item below, previously open) that the 0.80 floor might be unachievable on IEX.

**Step 3 — point-in-time spot-checks (calibrated config).** 2025-07-02 (~1y back): 970
passed, `fail_coverage` 885 (survivorship shrinkage already visible). 2024-07-02 (~2y
back): **refused** — `ScanCoverageError`, only 79% coverage. 2022-07-01 (~4y back):
**refused** — 60% coverage. A deliberate weekend check, 2026-07-05 (Sunday): **refused** —
0% coverage, exactly as designed. Interpretation: PIT reconstruction with the
current-only asset list is trustworthy roughly 1 year back; beyond that, survivorship
(symbols listed since `as_of` have no bars at that date under the current-asset-list
limitation) drives measured coverage below the 0.80 floor and the scan refuses rather than
emitting a biased snapshot. A deep-PIT study must consciously lower `min_coverage_fraction`
and accept the disclosed survivorship bias — this was the spec's disclosed v1 limit; it now
has measured decay numbers behind it.

**Step 4 — first live nightly run with onboarding: NOT RUN, impossible on a Sunday.** A
live `as_of=today` run refuses on the weekend coverage floor, and a backdated run correctly
skips screener capture + onboarding by design (see the backdated-run guard above) — there
is no valid way to exercise the live onboarding path on 2026-07-05. This is the one
remaining item of Task 9's brief, deferred to the next trading session (Mon 2026-07-06,
17:00 ET / 16:00 CT cron). Expected on that run, per the design: most of the top-10
most-actives get gate-filtered (ETFs/sub-$10 names dominate raw most-actives lists), a
zero-candidate night is a valid outcome (`onboarded=[]`, no run launched), a qualifying
candidate's dual backfill takes ~2-5 min each, and a tagged `onboarding-<date>` run appears
in the runs-store (`status=queued->running->succeeded`, visible via `list_runs`).

**Data hygiene note.** The first (pre-fix) run on 2026-07-05 captured Sunday's screener
payload keyed under scan_date 2026-07-02 (a real-time-only screener call, saved under the
wrong date before the backdated-run guard existed) — those 3 `screener_snapshots` rows were
deleted to keep the archive honest (`captured_at` was preserved as evidence of the
incident). The backdated-run guard (fix-round commit `47e6f56`) now prevents recurrence.
The screener archive therefore starts genuinely empty; the first real capture will be
2026-07-06.

## M10: universe 500 + backtest campaign

A third axis of work alongside M9's discovery half and M8's presentation layer: **can the
M7.5-promoted config's tiny-sample findings survive a 4x larger, less curated universe?**
Spec/plan: `docs/superpowers/specs/2026-07-05-universe-500-campaign-design.md` /
`docs/superpowers/plans/2026-07-04-m7.5-round1-round4.md` sibling plan committed alongside
M8's (`744eddf`). Built as 6 implementer tasks (commits `ccda502..ace98c3`) plus a final
whole-branch review (run jointly against M8, since both landed around the same time) and a
4-commit fix round, then executed as a real campaign against real data (not just built and
left idle, per this repo's usual pattern of closing a milestone with a real run).

**What was built:**

- `BacktestConfigM5.universe_file` / `trade_symbols_override` (`ccda502`) — two new
  event-loop-only config fields, inert in the engine itself (the M5 engine still trades
  whatever `PreparedM5` was built from); `run_backtest_job.py` and the campaign driver
  consume them, and an override is validated against the prepared universe rather than
  silently ignored if it names an untraded symbol.
- `scan/universe500.py` + `scripts/build_universe_500.py` — selects the top-up 372 by
  `adv_dollars` from the latest scan snapshot (refusing if the snapshot is >7 days stale),
  restricted to symbols with a first daily bar on or before 2021-07-05 (5-year backtest
  history requirement), and writes the committed `config/universe_500.yaml`
  (128 curated + 372 top-up, `scan_date=2026-07-02`).
- **Sector enrichment — a disclosed vendor substitution.** The plan assumed `yfinance`
  would supply sector labels for the 372 top-up symbols; in practice yfinance is
  **hard-blocked by Yahoo** (HTTP 401 "Invalid Crumb" / anti-scraping feature-block,
  reproduced against the latest yfinance 1.5.1, 0/372 symbols resolved — not a transient
  outage). `scripts/enrich_sectors.py` was rewritten (`4ba4d92`) against **Nasdaq's public
  screener API** instead: one HTTP request via stdlib `urllib` (no new dependency) returns
  sector labels for ~6,400 symbols; class-share tickers are translated dot-to-slash
  (`BRK.B` -> `BRK/B`) to match Nasdaq's convention. `config/sectors_500.yaml` is the
  committed one-shot output (`_source: nasdaq-screener 2026-07-05`); 1 symbol (`BRK.B`)
  stays `UNKNOWN` (Nasdaq's own listing row for it is empty).
- `backtest/campaign.py` — `VARIANTS` (baseline/w12/w24/hold2/shorts, each a
  `dataclasses.replace` delta), `split_cohorts` (deterministic sector-stratified
  round-robin — sorts by `(sector, symbol)` before dealing so each sector spreads across
  all cohorts), `campaign_label_re` (exact-match `m10-{tag}-{variant}-c<digits>` pattern —
  see the "Also fixed" bullet below for why this matters), `poll_and_launch` (detached-job
  polling with dead-process detection + a status-recheck race guard), a duplicate-launch
  guard, and `--resume` to reattach after a Ctrl-C or crash.
- `backtest/aggregate.py` — pools a variant's cohort runs into one metrics view via
  `compute_metrics`; **refuses** to pool a campaign with any missing/unfinished/failed
  cohort run (`CampaignIncompleteError`) rather than silently understating sample size.
  Equity across cohorts is summed on the union index with `ffill().bfill()` — a documented
  approximation (flat-lines a cohort's pre-start capital rather than truly having none;
  fine for drawdown shape, not a literal portfolio simulation).
- `scripts/run_campaign_500.py` / `scripts/aggregate_campaign.py` — the CLI driver and
  pooling entry points.

**Final whole-branch review (run jointly with M8) caught 2 cross-task Criticals before
launch** — both would have made the real campaign run either impossible or misleading:

1. **Cohort jobs would have loaded all 500 symbols' bars per process** (`39aac35`) — each
   of the 20 cohort jobs would have paid `_prepare_m5` on the full universe file instead of
   just its own ~125-symbol cohort, an estimated 15-20 GB/process that would have OOM'd
   long before the campaign finished (the M7.5 sweep had already OOM'd at just 130 symbols
   in one process). Fixed so `_load_symbols` = benchmarks + the run's `trade_symbols`,
   verified to produce identical results to the pre-fix default path on a control run.
2. **The un-enriched, all-`UNKNOWN`-sector universe file would have silently strangled the
   top-up selection** (`0302364`) — `max_per_sector=2` treating all 372 top-up symbols as
   one sector would have capped the tradeable list at effectively the 128 curated symbols
   plus 2 top-up names. `build_universe_500.py` now refuses to write a universe file with
   >10% `UNKNOWN` sectors unless `--allow-unknown` is passed explicitly.

**Also fixed in the same review round**: unanchored `LIKE` label matching in
`find_campaign_runs` (a `baseline` query could silently pool in a `baseline-cool-w12`
cohort's run — fixed by post-filtering every SQL hit through `campaign_label_re.fullmatch`,
`721e22f`); `poll_and_launch` hanging forever if a job died before ever reaching
`mark_running` (`ae24d7f`); no way to reattach to an in-flight campaign after an
interruption (`bb5deb9`, `--resume`); and the equity-pooling `bfill` approximation was
undocumented (`69f278e`).

**Backfill.** The 372 new top-up symbols' daily + minute bars backfilled in **~2 hours**
(the plan had budgeted a feared 12-30 hours) — the warehouse grew 3.1 -> 9.8 GB,
141,382,373 minute rows / 627,357 daily rows / 502 symbols total, **0 manifest error
units**. The nightly cron was paused for the duration via the `.nightly_paused` pause-file
switch added to the cron wrapper (`a69e9f9`) and unpaused once the backfill finished.

**Campaign execution.** Tag `jul05`: 4 cohorts x 5 variants = 20 cohort runs, all 20
succeeded, `max_parallel=2` (~1.9 GB/process, comfortably under the 24 GB budget). One real
operational incident: the first driver process was externally killed mid-run, leaving 2
rows stuck at `status='running'` in Postgres with no process actually running them (the
exact "stale-run" scenario M8's docs flagged as a known v1 gap) — these were re-queued
manually and the driver relaunched detached; that relaunch was `--resume`'s first real
use, and it correctly adopted all already-queued runs from the interrupted attempt instead
of erroring on the duplicate guard or re-creating them.

**Results** (pooled via `aggregate_campaign`, full table + reasoning in
`docs/tuning/m7.5-tuning-matrix.md` §6 "M10 out-of-universe re-check"; raw rows in
`docs/tuning/ledger.csv` as `m10-jul05-<variant>`):

| variant | n_trades | win_rate | PF | total_pnl |
|---|---|---|---|---|
| baseline (w18, hold1 — the M7.5 promoted config) | 34 | 38.2% | 0.86 | -$240 |
| w12 (spec default window) | 14 | 35.7% | 1.91 | +$438 |
| w24 | 51 | 37.3% | 1.10 | +$257 |
| hold2 (old bias-hold behavior) | 28 | 32.1% | 0.53 | -$760 |
| shorts | 37 | 35.1% | 0.77 | -$439 |

Reference: at 130 symbols the promoted baseline measured 13 trades / PF 3.71 / +$753 / 58%
win rate (ledger row `r4a-w18-t1-s1`).

**Conclusions, with the honesty this repo's norms demand:**

1. **The `rrs_m5_window=18` promotion INVERTED out-of-universe**: the M7.5 peak (w18) is
   the 500-symbol trough, and the spec default `w12` performs best. The M7.5 promotion
   looks curated-universe-overfit — every M7.5 result carried the "directional, tiny
   sample" caveat explicitly, and this re-check vindicates having kept that caveat rather
   than treating w18 as settled. No config default has been changed as a result of this
   finding; it is recorded as a finding pending a decision, not acted on unilaterally.
2. **The `bias_hold_bars=1` promotion SURVIVES**: `hold2` (the old default) is decisively
   worse (PF 0.53 vs baseline's 0.86) under the identical w18 window — the opposite
   direction from the window result, i.e. this is not a blanket "everything M7.5 promoted
   was wrong" story.
3. **Shorts remain weak at 500** (PF 0.77), consistent with M7's ablation (5/6 short trades
   lost) and the M7.5 study suite (shorts weak in every gate-ablation bucket) — continued,
   now larger-sample evidence for deprioritizing the short algo rather than tuning it.
4. **Interpretation caveats**: samples are still modest (14-51 trades, below this
   campaign's own ≥30/≥100 readability milestones for some variants); cohort structure is a
   real confound, not just a sample-size multiplier — each symbol now competes against ~93
   new cohort-mates for the selection engine's per-day/per-sector slots, and portfolio-level
   constraints (concurrency, loss limits) apply per cohort, not globally across the full
   500 (`backtest/campaign.py` module docstring); the 372 top-up symbols have no earnings
   blackout populated (`reference_overrides.yaml` is curated-universe-only).
5. **DEFERRED**: the full M7-style validation-study suite (ablation, walk-away, RRS
   sensitivity, bias confusion, time-of-day) has not been re-run at 500 symbols —
   `scripts/run_validation_studies.py` has no universe-override support today (the
   original plan's assumption that it did was incorrect); retrofitting it is a candidate
   next task, recorded here as a real limitation rather than silently skipped.

**Known-limitations additions from this milestone** (folded into the numbered list below,
items 33-36): sector-vocabulary mismatch between Nasdaq's labels and the curated universe's
labels; the earnings-blackout gap for the 372 top-up symbols; the cohort-semantics caveat
(per-cohort, not global, portfolio constraints); and the study-suite universe-override
retrofit still needed.
