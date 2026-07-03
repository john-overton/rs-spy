# Implementation Status

Status snapshot for resuming work. Specs live in `algo-spec/` (the *what/why*);
this document is the *what's actually built* (the *how*, and where it
deviates from spec). Written at the M4 checkpoint. Read "Critical: a real
timezone bug affected all data before this point" before trusting any date
in an earlier report (trade dates, trigger dates, etc.) — the underlying
OHLCV values and all numeric results were never wrong, only date labels.

## TL;DR

A working, real-data-verified, end-to-end **daily-bar (D1)** backtest of the
RS/RW system runs cleanly (90 passing tests, lint clean) against 5+ years of
cached Alpaca daily bars for a **130-symbol** curated universe (grown from 28
during M3.5). M3.5 found real signal (437 walk-away entry signals, stable
~1.9R mean MFE; an RRS-window sensitivity sweep favoring `window=3` over the
M3 default of 5) but also revealed that the D1 approximation trades on
multi-day/multi-week holds (5-35 days) — a fundamentally different shape
from the actual intraday spec, so tuning it further wasn't worth continued
investment. **M4 (this checkpoint) backfilled 5 years of minute bars for
the full 130-symbol universe (44.1M rows)** and, in the process, found and
fixed two real bugs: a timezone bug that silently corrupted every stored
bar's date/time (see below — serious, now fixed, warehouse fully rebuilt),
and an RVOL design flaw (arrival-order indexing breaks on IEX's frequent
data gaps for less-liquid names). Also built ahead of schedule: the three
M5-only indicators with no D1 equivalent (VWAP, RVOL, Laguerre RSI),
tested and ready for the M5 selection/bias engine build. Full M5
bias/selection engines and the M5-cadence backtest engine are **not started**
— that's M5/M6.

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

python -m pytest -q              # 90 tests, hermetic, no network/credentials needed
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

  selection/
    features.py                 # compute_symbol_features(): per-symbol D1 feature DataFrame (rrs, ha_cont, etc.)
    gates.py                      # D1-available hard gates (G1,G4,G5,G6,G7,G8; G2/G3/G9 dropped, no D1 equivalent)
    scoring.py                     # score_long/score_short, W1->W2 weight redistribution (see below)
    watchlist.py                    # IDLE/QUALIFIED/DIP_ARMED/ENTRY_EVAL state machine, build_tradeable_list()

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

tests/unit/       # 13 files, golden + property(hypothesis) + no-lookahead causality + M3.5 study smoke tests, all hermetic
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

65 tests, all hermetic (no network calls, no real credentials needed):

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
   Worth revisiting for the *M5* RRS window default (`RRS_M5_WINDOW`, spec value L=12) once that's
   built, since the same sensitivity-sweep methodology applies there too.
7. `indicators/vwap.py` and `indicators/rvol.py` require pre-filtered RTH-only input and will
   silently produce wrong (not erroring) results if called on raw minute bars that still include
   pre/post-market rows -- always go through `data.loader.load_minute_bars()` (RTH-filtered by
   default) rather than querying `bars` directly for `timespan='minute'`.
8. `indicators/rvol.py` will be NaN often for illiquid names/times (see deviation #10) --
   expected, but means RVOL-based gates in the M5 selection engine will need a documented
   fallback (e.g. treat NaN as "gate fails" rather than raising) once built.
9. `data/warehouse.duckdb` is now ~3.2GB (44.1M minute rows dominate). Fine for local dev; worth
   knowing before assuming this fits an ad hoc backup/sync workflow.

## Next: M5 (full intraday indicator/bias/selection engines)

Per the plan: build the real M5-cadence system now that minute data is cached and the three
M5-only indicators (VWAP, RVOL, Laguerre RSI) are built and tested. Remaining M5 scope: M5
trendlines/breach trigger (reuse `indicators/trendlines.py` with `strength=3, min_gap=6` per
spec -- likely no new code needed, just different call parameters at M5 cadence), the H1-basis
ATR that M5 RRS needs (`ATR(H1, 50)` -- requires resampling M5 bars to H1, not yet built), the
full 8-component M5 bias engine (03), the full 9-gate M5 selection engine including the two
gates dropped from the D1 version (G2 M5 RS, G3 VWAP) plus the deferred G9 (QQQ cross-check), and
the LRSI-based dip-arm mechanism (05 §3's real version, vs. D1's raw-RRS-crossing proxy). This is
substantially more code than the D1 walking skeleton was -- worth scoping/sequencing explicitly
with the user before starting, rather than assuming the D1 module structure just extends
cleanly to M5 cadence.
