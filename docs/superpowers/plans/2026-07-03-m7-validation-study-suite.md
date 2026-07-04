# M7: Full Validation Study Suite (M5 Cadence) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build algo-spec 08 §3's full validation study suite (rule ablation, walk-away analysis,
RRS sensitivity, bias-engine confusion matrix, time-of-day/regime slicing) at true M5 cadence,
plus one CLI command that runs all five and writes `reports/m7_studies/`, per M7's milestone
done-when criterion. The D1-cadence versions of studies 3.1-3.3 already exist
(`backtest/studies/ablation.py`/`walk_away.py`/`rrs_sensitivity.py`, built during M3.5) -- this
plan adds M5-cadence siblings (`*_m5.py`), not replacements; the D1 versions and
`scripts/run_validation_studies_m35.py` stay as historical precedent, unchanged.

**Context:** M6 built the M5 backtest engine and long/short algo. Its first real run produced 0
trades; M7 pre-work (already complete, commits `ad2ee2b`..`d49ef86`) fixed a real ADV-gate
cadence bug (0 -> 3 trades) and built a committed full-universe gate-pass-rate/watchlist-state
audit confirming gate confluence is genuinely rare (joint pass rate ~0.01-0.02%) and that 100% of
realized trades enter via the 04 §6 trigger-bypass path, never the "own dip" state machine. Per
the user's explicit direction, this plan proceeds to build the full study suite now, against the
honest ~3-trade sample -- mirroring M3.5's own precedent of running its D1 studies on a small
sample (8 trades) and reporting findings as directional, not statistical proof.

**Architecture:** Each of the 5 studies is a pure, testable module in `backtest/studies/` (mirrors
the existing D1 pattern exactly -- see `ablation.py`/`walk_away.py`/`rrs_sensitivity.py` for the
established style: plain functions, no CLI/file I/O in the study module itself). A single CLI
script (`scripts/run_validation_studies.py`) computes ONE shared baseline `PreparedM5` +
`BacktestResultM5` (via `engine_m5._prepare_m5`/`run_m5_backtest`) and threads it into the
walk-away and time-of-day studies (both need already-computed backtest state, not fresh runs),
while ablation (needs 6 additional gate-disabled re-runs) and RRS sensitivity (needs 9 additional
window/threshold re-runs) each run their own additional full backtests. Bias confusion needs no
backtest run at all (just SPY/QQQ bias + forward price data).

**Tech Stack:** Same as the rest of the repo -- pandas/numpy, pytest, no new dependencies.

## Global Constraints

- **Runtime is real and must stay disclosed, not hidden.** The M5 precompute layer
  (`engine_m5._prepare_m5`, via `selection.features_m5.compute_symbol_features_m5`) is
  deliberately non-vectorized for a few indicators (Laguerre RSI, trendlines, headroom -- see
  `algo-spec/02`'s own stated exception and this repo's `README.md`) and takes on the order of
  15-20 minutes per full-universe run. The complete study suite needs roughly 16 such runs (1
  shared baseline + 6 ablation disables + 9 RRS sensitivity combos) -- expect several hours for a
  real, full-universe run. Every module and the CLI script's docstring must state this plainly.
  Unit tests use tiny synthetic universes (a handful of bars/symbols, following
  `tests/unit/test_engine_m5_backtest.py`'s fixture pattern) and must stay fast (seconds, not
  minutes) -- this constraint is about the CLI's real-data run, not the test suite.
- **"Long and short reported separately"** (the M7 milestone's own done-when text, and
  algo-spec 08 §3's own framing) -- every study that produces per-trade or per-signal output must
  report LONG and SHORT results as distinct summaries, not pooled together. Use
  `BacktestConfigM5(shorts_enabled=True)` as every study's base config so both books actually
  trade in the underlying backtest runs.
- **Forward-looking windows in the walk-away and bias-confusion studies are intentional, not a
  lookahead bug.** Both studies deliberately look at what happens in bars i+1..i+horizon relative
  to a signal at bar i -- this is the entire point of a walk-away/predictive-power study (unlike
  every indicator/feature/gate in `indicators/`, `selection/`, and `algo/`, which must never look
  forward). Do not "fix" the forward window in either study; do keep every gate/indicator/feature
  computation elsewhere exactly as backward-looking as it already is.
- Every new study module goes in `src/rs_spy/backtest/studies/`, named `<d1_name>_m5.py` to
  mirror the existing D1 modules (`ablation_m5.py`, `walk_away_m5.py`, `rrs_sensitivity_m5.py`),
  or a new name for the two studies with no D1 precedent (`bias_confusion_m5.py`,
  `time_of_day_m5.py`).
- Read `src/rs_spy/backtest/engine_m5.py`, `src/rs_spy/selection/gates.py`,
  `src/rs_spy/selection/watchlist.py`, and the existing D1 study modules in full before writing
  each task's code below -- this plan's code snippets were written against the current state of
  those files, but always verify signatures against the real current file, not just this plan's
  text, exactly as this project's prior M6 plan required of its implementers.

---

### Task 1: `BacktestConfigM5` RRS threshold config knobs

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py`
- Test: `tests/unit/test_engine_m5_backtest.py`

**Interfaces:**
- Consumes: `gates.gates_pass_long_m5`/`gates_pass_short_m5`'s existing `rrs_m5_threshold`/
  `rrs_d1_threshold` parameters (already present in `selection/gates.py`, just never overridden
  by `_prepare_m5` before this task).
- Produces: `BacktestConfigM5.rrs_m5_threshold_long`/`rrs_m5_threshold_short`/
  `rrs_d1_threshold_long`/`rrs_d1_threshold_short` -- new fields Task 4 (RRS sensitivity sweep)
  depends on to vary the RRS gate threshold per sweep combination.

Read `src/rs_spy/backtest/engine_m5.py`'s current `BacktestConfigM5` dataclass (around line
44-64) and `_prepare_m5`'s two `gates.gates_pass_long_m5`/`gates_pass_short_m5` calls (around
line 138-151) before editing -- match the real current field list and call sites exactly.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_engine_m5_backtest.py` (reuse the existing `universe` fixture in that
file -- read the file's first ~62 lines first for the fixture's exact shape):

```python
def test_prepare_m5_honors_rrs_m5_threshold_config(universe):
    loose = BacktestConfigM5(rrs_m5_threshold_long=-100.0)  # impossible to fail
    strict = BacktestConfigM5(rrs_m5_threshold_long=100.0)  # impossible to pass
    prepared_loose = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=loose,
    )
    prepared_strict = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=strict,
    )
    # A threshold of -100 on the RRS gate can never fail (rolling_rrs_m5 is always >= -100);
    # a threshold of +100 can never pass. If the config field isn't actually threaded through,
    # both runs would produce identical (default-threshold) gate_long series.
    assert prepared_loose.gate_long["AAPL"].sum() >= prepared_strict.gate_long["AAPL"].sum()
    assert not prepared_strict.gate_long["AAPL"].any()
```

Also add the mirrored short-side + D1-threshold-field test to prove all four new fields thread
through (default `BacktestConfigM5()` has `shorts_enabled=False`, so pass
`shorts_enabled=True` explicitly, and use `above_vwap`-style feature control isn't available here
since this test uses the real `universe` fixture, not `test_gates_m5.py`'s hand-built features --
adjust thresholds, not fixture features, to force the pass/fail split):

```python
def test_prepare_m5_honors_rrs_d1_threshold_config_both_directions(universe):
    strict_long = BacktestConfigM5(rrs_d1_threshold_long=100.0)
    strict_short = BacktestConfigM5(shorts_enabled=True, rrs_d1_threshold_short=-100.0)
    prepared_long = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]}, universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]}, spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"],
        spy_d1=universe["spy_d1"], qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"}, config=strict_long,
    )
    prepared_short = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]}, universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]}, spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"],
        spy_d1=universe["spy_d1"], qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"}, config=strict_short,
    )
    assert not prepared_long.gate_long["AAPL"].any()
    assert not prepared_short.gate_short["AAPL"].any()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -k rrs_threshold -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'rrs_m5_threshold_long'`
(the field doesn't exist yet).

- [ ] **Step 3: Add the config fields and thread them through `_prepare_m5`**

In `BacktestConfigM5`, add these four fields (after the existing `use_qqq_crosscheck: bool = False`
line -- read the real current field order first, insert in a sensible place near the other gate-
related fields):

```python
    rrs_m5_threshold_long: float = 1.0
    rrs_m5_threshold_short: float = -1.0
    rrs_d1_threshold_long: float = 1.0
    rrs_d1_threshold_short: float = -1.0
```

These defaults exactly match `gates_pass_long_m5`/`gates_pass_short_m5`'s own existing default
parameter values, so this change is behavior-preserving for every existing caller until a caller
explicitly overrides one of the four new fields.

In `_prepare_m5`, update the two gate calls (read the real current call sites first -- they were
last modified by the ADV-gate fix task, which added `adv20=adv20_native`; add the new keyword
arguments alongside it, do not remove or reorder any existing argument):

```python
        gl_native = gates.gates_pass_long_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_long,
            rrs_d1_threshold=config.rrs_d1_threshold_long,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
            adv20=adv20_native,
        ).fillna(False)
        gs_native = gates.gates_pass_short_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_short,
            rrs_d1_threshold=config.rrs_d1_threshold_short,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
            adv20=adv20_native,
        ).fillna(False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -v`
Expected: all pass, including the 2 new tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, clean.

```bash
git add src/rs_spy/backtest/engine_m5.py tests/unit/test_engine_m5_backtest.py
git commit -m "Add RRS threshold config knobs to BacktestConfigM5, threaded through _prepare_m5"
```

---

### Task 2: `backtest/studies/ablation_m5.py` -- 08 §3.1 M5 gate-count ablation

**Files:**
- Create: `src/rs_spy/backtest/studies/ablation_m5.py`
- Test: `tests/unit/test_studies_m5.py`

**Interfaces:**
- Consumes: `engine_m5.BacktestConfigM5`/`run_m5_backtest`/`PreparedM5`/`TradeM5` (Task 1's config
  fields are not used here -- this task ablates `disabled_gates`, not RRS thresholds), `selection
  .gates.HARD_RULE_NAMES` and `gate_rrs_long/short`, `gate_ha_long/short`, `gate_sma_long/short`,
  `gate_rrs_m5_long/short`, `gate_vwap_long/short`, `bias.buckets.{BULL,STRONG_BULL,BEAR,STRONG_BEAR}`.
- Produces: `run_gate_ablation_m5(universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1,
  qqq_m1, qqq_m5, sectors, earnings_blackout, base_config, baseline_prepared, baseline_result) ->
  dict` with keys `"trades"` (DataFrame), `"summary_long"` (DataFrame), `"summary_short"`
  (DataFrame), `"run_trade_counts"` (dict) -- Task 6 (CLI) calls this directly.

`baseline_prepared`/`baseline_result` are accepted as parameters (already computed by the caller
with `base_config`) specifically so this function does NOT need to redundantly recompute the
expensive M5 precompute layer for its own baseline run -- see this plan's Global Constraints
section on runtime. This function still runs 6 fresh full backtests internally (one per
individually-disabled hard rule -- those genuinely need fresh runs, since `disabled_gates`
changes what trades occur).

Read `src/rs_spy/backtest/studies/ablation.py` (the D1 precedent) in full first -- this task is
its M5-cadence, both-directions sibling. Read `src/rs_spy/selection/gates.py`'s
`HARD_RULE_NAMES` definition and `gates_pass_long_m5`/`gates_pass_short_m5` to confirm which 6
names are ablatable at M5 cadence (`{"bias", "rrs", "ha", "sma", "rrs_m5", "vwap"}`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_studies_m5.py`. Reuse the `_m1_session`/`_build_m1`/`_build_d1`/`DATES`
helper pattern from `tests/unit/test_engine_m5_backtest.py` (read its first ~40 lines) -- copy a
small local version of these helpers into this new test file rather than importing across test
files (this project's existing convention, confirmed in the M7 gate-audit task's own test file).

```python
import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5, run_m5_backtest
from rs_spy.backtest.studies.ablation_m5 import HARD_RULES_M5, run_gate_ablation_m5


def _m1_session(date, n_minutes, start_price, drift, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{date} 09:30", periods=n_minutes, freq="1min", tz="America/New_York").tz_convert("UTC")
    noise = rng.normal(0, 0.05, n_minutes)
    close = start_price + np.cumsum(np.full(n_minutes, drift) + noise)
    high = close + np.abs(rng.normal(0.05, 0.02, n_minutes))
    low = close - np.abs(rng.normal(0.05, 0.02, n_minutes))
    open_ = close - drift - noise
    volume = rng.integers(500, 1500, n_minutes).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _build_m1(dates, n_minutes=390, start_price=100.0, drift=0.0, seed=1):
    frames = [_m1_session(d, n_minutes, start_price + i * drift * n_minutes, drift, seed + i) for i, d in enumerate(dates)]
    return pd.concat(frames)


def _build_d1(m1):
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_localize(None)
    return daily


DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=20)]


@pytest.fixture
def small_universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    qqq_m1 = _build_m1(DATES, drift=0.0006, seed=2)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)

    spy_m5, qqq_m5, aapl_m5 = resample_ohlcv(spy_m1, "5min"), resample_ohlcv(qqq_m1, "5min"), resample_ohlcv(aapl_m1, "5min")
    spy_d1, qqq_d1, aapl_d1 = _build_d1(spy_m1), _build_d1(qqq_m1), _build_d1(aapl_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "qqq_m1": qqq_m1, "qqq_m5": qqq_m5, "qqq_d1": qqq_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
    }


def test_run_gate_ablation_m5_returns_per_direction_summaries_and_run_counts(small_universe):
    u = small_universe
    config = BacktestConfigM5(shorts_enabled=True)
    universe_m1 = {"AAPL": u["aapl_m1"]}
    universe_m5 = {"AAPL": u["aapl_m5"]}
    universe_d1 = {"AAPL": u["aapl_d1"]}
    sectors = {"AAPL": "Technology"}

    baseline_prepared = _prepare_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )
    baseline_result = run_m5_backtest(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )

    result = run_gate_ablation_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, None, config, baseline_prepared, baseline_result,
    )

    assert result["run_trade_counts"]["baseline"] == len(baseline_result.trades)
    assert set(result["run_trade_counts"].keys()) == {"baseline", *[f"disable_{r}" for r in HARD_RULES_M5]}
    # Regardless of whether any trades exist in this tiny synthetic universe, the summary
    # frames must exist and be indexed over every possible rule_count 0..len(HARD_RULES_M5).
    if not result["trades"].empty:
        assert set(result["summary_long"]["rule_count"]) == set(range(len(HARD_RULES_M5) + 1))
        assert set(result["summary_short"]["rule_count"]) == set(range(len(HARD_RULES_M5) + 1))
```

**A second, more targeted test proving the rule-scoring itself is correct** (not just that the
plumbing runs without error) -- build a hand-crafted `TradeM5` and a `PreparedM5`-like object (or
call `_prepare_m5` on a fixture engineered so you know exactly which of the 6 rules pass/fail one
bar before a known trade's `entry_time`), and assert `_score_trades`'s (or whichever internal
helper you write) `rule_count` for that trade matches your own hand count. Design this test after
implementing Step 3 below, once you can see exactly which helper function needs direct unit
coverage -- do not skip it; a bug in the rule-scoring logic (e.g. checking the wrong bar index, or
swapping a long/short gate function) would otherwise not be caught by the end-to-end test above,
which doesn't independently verify `rule_count` values.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_studies_m5.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'rs_spy.backtest.studies.ablation_m5'`).

- [ ] **Step 3: Implement**

```python
"""M7 rule-count ablation study. algo-spec/08-backtesting-and-validation.md
§3.1 ("Keeping it Really Simple"), M5-adapted from M3.5's D1-cadence version
(ablation.py). Extends the D1 4-hard-rule set to the full M5 6-hard-rule set
(selection/gates.py's HARD_RULE_NAMES minus the D1-inapplicable subset):
market bias, RRS (D1), Heikin-Ashi continuation, SMA stack, RRS (M5), VWAP.

Re-runs the M5 backtest with each hard rule individually disabled (plus the
caller-supplied baseline run, to avoid a redundant extra full-universe
precompute -- see this module's docstring on `baseline_prepared`/
`baseline_result`). Every resulting trade (deduped by symbol/entry_time/
direction across all runs) is scored against the FULL, always-on 6-rule
yardstick at its own entry SIGNAL bar (one bar before the fill, matching
broker_sim.py's next-bar-fill convention -- the same fixed 1-bar-lag
approximation ablation.py's D1 precedent uses for D1's 1-day lag),
independent of which run produced it. Trades are bucketed by how many of
the 6 rules they satisfied and win rate/expectancy reported per bucket,
separately for LONG and SHORT (algo-spec 08 §3's "long and short reported
separately").

Spec's expectation: both should increase monotonically with rules
satisfied; a rule that doesn't improve results when present is suspect.
M3.5's D1 version (4 rules, 8 trades) found this uninformative -- no
ablated rule ever unlocked a new trade. Worth checking whether the fuller
M5 rule set, or a larger real trade count, behaves differently.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL
from rs_spy.selection import gates

HARD_RULES_M5 = ("bias", "rrs", "ha", "sma", "rrs_m5", "vwap")


def _rule_ok_long(prepared, sym: str, i: int) -> dict:
    feat = prepared.features[sym]
    return {
        "bias_ok": prepared.bias_df["bias"].iat[i] in (BULL, STRONG_BULL),
        "rrs_ok": bool(gates.gate_rrs_long(feat).iat[i]),
        "ha_ok": bool(gates.gate_ha_long(feat).iat[i]),
        "sma_ok": bool(gates.gate_sma_long(feat).iat[i]),
        "rrs_m5_ok": bool(gates.gate_rrs_m5_long(feat).iat[i]),
        "vwap_ok": bool(gates.gate_vwap_long(feat).iat[i]),
    }


def _rule_ok_short(prepared, sym: str, i: int) -> dict:
    feat = prepared.features[sym]
    return {
        "bias_ok": prepared.bias_df["bias"].iat[i] in (BEAR, STRONG_BEAR),
        "rrs_ok": bool(gates.gate_rrs_short(feat).iat[i]),
        "ha_ok": bool(gates.gate_ha_short(feat).iat[i]),
        "sma_ok": bool(gates.gate_sma_short(feat).iat[i]),
        "rrs_m5_ok": bool(gates.gate_rrs_m5_short(feat).iat[i]),
        "vwap_ok": bool(gates.gate_vwap_short(feat).iat[i]),
    }


def _score_trades(prepared, trades) -> pd.DataFrame:
    calendar = prepared.calendar
    rows = []
    for t in trades:
        if t.symbol not in prepared.features or t.entry_time not in calendar:
            continue
        entry_idx = calendar.get_loc(t.entry_time)
        signal_idx = entry_idx - 1
        if signal_idx < 0:
            continue
        checks = (
            _rule_ok_long(prepared, t.symbol, signal_idx) if t.direction == "LONG"
            else _rule_ok_short(prepared, t.symbol, signal_idx)
        )
        rows.append({
            "symbol": t.symbol, "direction": t.direction,
            "entry_time": t.entry_time, "signal_time": calendar[signal_idx],
            "pnl": t.pnl, "r_multiple": t.r_multiple,
            **checks,
            "rule_count": sum(checks.values()),
        })
    return pd.DataFrame(rows)


def run_gate_ablation_m5(
    universe_m1: dict, universe_m5: dict, universe_d1: dict,
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None,
    base_config: BacktestConfigM5,
    baseline_prepared,
    baseline_result,
) -> dict:
    earnings_blackout = earnings_blackout or {}

    all_trades = list(baseline_result.trades)
    seen = {(t.symbol, t.entry_time, t.direction) for t in all_trades}
    run_trade_counts = {"baseline": len(baseline_result.trades)}

    for rule in HARD_RULES_M5:
        cfg = replace(base_config, disabled_gates=frozenset(base_config.disabled_gates | {rule}))
        result = run_m5_backtest(
            universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
            sectors, earnings_blackout, cfg,
        )
        run_trade_counts[f"disable_{rule}"] = len(result.trades)
        for t in result.trades:
            key = (t.symbol, t.entry_time, t.direction)
            if key in seen:
                continue
            seen.add(key)
            all_trades.append(t)

    scored = _score_trades(baseline_prepared, all_trades)
    if scored.empty:
        return {
            "trades": scored, "summary_long": pd.DataFrame(), "summary_short": pd.DataFrame(),
            "run_trade_counts": run_trade_counts,
        }

    summaries = {}
    for direction in ("LONG", "SHORT"):
        sub = scored[scored["direction"] == direction]
        if sub.empty:
            summaries[direction] = pd.DataFrame(columns=["rule_count", "n_trades", "win_rate", "avg_r", "expectancy"])
            continue
        summaries[direction] = (
            sub.groupby("rule_count")
            .agg(n_trades=("pnl", "size"), win_rate=("pnl", lambda s: (s > 0).mean()),
                 avg_r=("r_multiple", "mean"), expectancy=("pnl", "mean"))
            .reindex(range(len(HARD_RULES_M5) + 1))
            .reset_index()
        )

    return {
        "trades": scored, "summary_long": summaries["LONG"], "summary_short": summaries["SHORT"],
        "run_trade_counts": run_trade_counts,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_studies_m5.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`

```bash
git add src/rs_spy/backtest/studies/ablation_m5.py tests/unit/test_studies_m5.py
git commit -m "Add M5-cadence gate ablation study (08 §3.1), long and short reported separately"
```

---

### Task 3: `backtest/studies/walk_away_m5.py` -- 08 §3.2 M5 walk-away analysis

**Files:**
- Create: `src/rs_spy/backtest/studies/walk_away_m5.py`
- Test: `tests/unit/test_studies_m5.py` (append)

**Interfaces:**
- Consumes: `engine_m5.PreparedM5`/`BacktestConfigM5`, `selection.watchlist.{IDLE,QUALIFIED,
  next_state_long,next_state_short}`, `algo.risk.STOP_ATR_MULT`.
- Produces: `run_walk_away_m5(prepared, realized_trades, config, horizon_bars=78) -> dict` with
  keys `"signals"` (DataFrame) and `"realized_trades"` (the same DataFrame passed in, returned
  for convenience so callers have one dict with both halves of the comparison). Unlike the D1
  precedent, this function takes an already-computed `PreparedM5` and realized-trades DataFrame
  as parameters rather than raw universe dicts, so the CLI (Task 6) can compute ONE shared
  baseline run and reuse it here and in the time-of-day study (Task 5) -- see this plan's Global
  Constraints on runtime.

Read `src/rs_spy/backtest/studies/walk_away.py` (the D1 precedent) in full first.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_studies_m5.py` (reuses this file's `small_universe` fixture from
Task 2):

```python
from rs_spy.backtest.studies.walk_away_m5 import run_walk_away_m5


def test_run_walk_away_m5_returns_signals_and_realized_trades(small_universe):
    u = small_universe
    config = BacktestConfigM5(shorts_enabled=True)
    universe_m1, universe_m5, universe_d1 = {"AAPL": u["aapl_m1"]}, {"AAPL": u["aapl_m5"]}, {"AAPL": u["aapl_d1"]}
    sectors = {"AAPL": "Technology"}

    prepared = _prepare_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )
    result = run_m5_backtest(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )
    trades = result.trades_df()

    walk_away = run_walk_away_m5(prepared, trades, config, horizon_bars=20)
    signals = walk_away["signals"]
    assert walk_away["realized_trades"] is trades
    if not signals.empty:
        assert set(signals["direction"]).issubset({"LONG", "SHORT"})
        assert (signals["horizon_bars"] <= 20).all()
        # An MFE at or above the MAE is a basic sanity invariant regardless of direction --
        # both are computed from the same window against the same entry price.
        assert (signals["mfe_r"] >= signals["mae_r"]).all()
```

**A second, targeted test proving MFE/MAE sign and magnitude are correct** -- build a tiny
hand-crafted `bars[sym]` window (a `PreparedM5`-shaped object is heavier than needed; instead
call the internal MFE/MAE row-building helper directly, whatever you name it, with a hand-built
`bars` DataFrame and a known `atr`/`entry_idx`) where you know the exact high/low values over the
horizon, and assert the returned `mfe_r`/`mae_r` match your own hand-computed
`(price_move) / (risk.STOP_ATR_MULT * atr)` values exactly -- for BOTH long and short (the sign
flip between the two is the single most likely place for a copy-paste bug). Write this test after
implementing Step 3, once the internal helper's real name and signature are visible.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_studies_m5.py -k walk_away -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
"""M7 walk-away analysis. algo-spec/08-backtesting-and-validation.md §3.2,
M5-adapted from M3.5's D1-cadence version (walk_away.py) -- see that module
for the full method description; identical idea, M5 cadence and both
directions.

For every M5 bar where a symbol's watchlist state transitions IDLE ->
QUALIFIED (an "entry signal," independent of whether a real trade later
took the "own dip" DIP_ARMED path or the 04 §6 trigger-bypass exception --
QUALIFIED is upstream of both, so this definition is unaffected by which
path a real trade eventually used), records the maximum favorable/adverse
excursion (MFE/MAE) over the following `horizon_bars` M5 bars, expressed in
the same R units as engine_m5.py's realized r_multiple (price move /
(risk.STOP_ATR_MULT * entry-bar ATR)), had a position been entered at the
NEXT bar's open and simply held with no active management. Comparing this
"walk away and do nothing" distribution against the realized trades'
r_multiple distribution indicates how much of the system's P&L is
determined by exit rules vs. stock/timing picks.
"""
import pandas as pd

from rs_spy.algo import risk
from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.selection import watchlist

DEFAULT_HORIZON_BARS = 78  # ~1 RTH session at M5 cadence (390 min / 5)


def _entry_signals_m5(prepared, direction: str, config: BacktestConfigM5) -> list[tuple]:
    gate = prepared.gate_long if direction == "LONG" else prepared.gate_short
    score = prepared.score_long if direction == "LONG" else prepared.score_short
    next_state_fn = watchlist.next_state_long if direction == "LONG" else watchlist.next_state_short
    n_bars = len(prepared.calendar)

    signals = []
    for sym in gate:
        rrs = prepared.features[sym]["rolling_rrs_m5"]
        lrsi = prepared.features[sym]["lrsi_m5"]
        state = watchlist.IDLE
        for i in range(n_bars):
            gp = bool(gate[sym].iat[i]) if not pd.isna(gate[sym].iat[i]) else False
            sc = score[sym].iat[i]
            rrs_prev = rrs.iat[i - 1] if i > 0 else None
            lrsi_prev = lrsi.iat[i - 1] if i > 0 else None
            new_state = next_state_fn(
                state, gp, sc, rrs_prev, rrs.iat[i],
                lrsi_prev=lrsi_prev, lrsi_now=lrsi.iat[i],
                min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
            )
            if state == watchlist.IDLE and new_state == watchlist.QUALIFIED:
                signals.append((sym, i))
            state = new_state
    return signals


def _walk_away_rows(prepared, direction: str, signals: list[tuple], horizon_bars: int) -> pd.DataFrame:
    calendar = prepared.calendar
    n_bars = len(calendar)
    rows = []
    for sym, i in signals:
        entry_idx = i + 1
        if entry_idx >= n_bars:
            continue
        atr = prepared.atr_m5[sym].iat[i]
        if pd.isna(atr) or atr <= 0:
            continue
        bars = prepared.bars[sym]
        entry_price = bars["open"].iat[entry_idx]
        if pd.isna(entry_price):
            continue
        r_basis = risk.STOP_ATR_MULT * atr
        end_idx = min(entry_idx + horizon_bars, n_bars - 1)
        window = bars.iloc[entry_idx : end_idx + 1]
        if window.empty or window["high"].isna().all():
            continue
        if direction == "LONG":
            mfe_r = (window["high"].max() - entry_price) / r_basis
            mae_r = (window["low"].min() - entry_price) / r_basis
        else:
            mfe_r = (entry_price - window["low"].min()) / r_basis
            mae_r = (entry_price - window["high"].max()) / r_basis
        rows.append({
            "symbol": sym, "direction": direction,
            "signal_time": calendar[i], "entry_time": calendar[entry_idx],
            "entry_price": entry_price, "mfe_r": mfe_r, "mae_r": mae_r,
            "horizon_bars": len(window) - 1,
        })
    return pd.DataFrame(rows)


def run_walk_away_m5(
    prepared, realized_trades: pd.DataFrame, config: BacktestConfigM5,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
) -> dict:
    long_signals = _entry_signals_m5(prepared, "LONG", config)
    short_signals = _entry_signals_m5(prepared, "SHORT", config)
    signals_df = pd.concat(
        [
            _walk_away_rows(prepared, "LONG", long_signals, horizon_bars),
            _walk_away_rows(prepared, "SHORT", short_signals, horizon_bars),
        ],
        ignore_index=True,
    )
    return {"signals": signals_df, "realized_trades": realized_trades}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_studies_m5.py -v`

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`

```bash
git add src/rs_spy/backtest/studies/walk_away_m5.py tests/unit/test_studies_m5.py
git commit -m "Add M5-cadence walk-away analysis study (08 §3.2), long and short"
```

---

### Task 4: `backtest/studies/rrs_sensitivity_m5.py` -- 08 §3.3 M5 RRS sensitivity sweep

**Files:**
- Create: `src/rs_spy/backtest/studies/rrs_sensitivity_m5.py`
- Test: `tests/unit/test_studies_m5.py` (append)

**Interfaces:**
- Consumes: Task 1's `BacktestConfigM5.rrs_m5_threshold_long/short` fields,
  `BacktestConfigM5.rrs_m5_window` (already existed pre-M7), `backtest.metrics.compute_metrics`/
  `metrics_by_direction`.
- Produces: `run_rrs_sensitivity_m5(universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1,
  qqq_m1, qqq_m5, sectors, earnings_blackout, base_config) -> pd.DataFrame`, one row per (window,
  threshold) combination with overall + per-direction metrics columns.

Read `src/rs_spy/backtest/studies/rrs_sensitivity.py` (the D1 precedent) and
`src/rs_spy/backtest/metrics.py`'s `compute_metrics`/`metrics_by_direction` signatures first.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_studies_m5.py`:

```python
from rs_spy.backtest.studies.rrs_sensitivity_m5 import THRESHOLDS, WINDOWS, run_rrs_sensitivity_m5


def test_run_rrs_sensitivity_m5_sweeps_every_combination(small_universe):
    u = small_universe
    universe_m1, universe_m5, universe_d1 = {"AAPL": u["aapl_m1"]}, {"AAPL": u["aapl_m5"]}, {"AAPL": u["aapl_d1"]}
    sectors = {"AAPL": "Technology"}

    sweep = run_rrs_sensitivity_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors,
    )
    assert len(sweep) == len(WINDOWS) * len(THRESHOLDS)
    assert set(sweep["window"]) == set(WINDOWS)
    assert set(sweep["threshold"]) == set(THRESHOLDS)
    for col in ("overall_n_trades", "long_n_trades", "short_n_trades"):
        assert col in sweep.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_studies_m5.py -k rrs_sensitivity -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
"""M7 RRS parameter sensitivity sweep. algo-spec/08-backtesting-and-validation.md
§3.3, M5-adapted from M3.5's D1-cadence version (rrs_sensitivity.py).

Sweeps RRS_M5_WINDOW (algo-spec 02/04's own {6, 12, 18} sweep for L) and the
M5 RRS gate qualification threshold, re-running the full M5 backtest for
each of the 9 combinations and collecting 08 §2 primary metrics, reported
both overall and separately by direction (algo-spec 08 §3's "long and short
reported separately").

Spec's expectation: the edge should be broad and stable across the sweep --
a sharp peak at one setting is a red flag for overfitting, not evidence of
good tuning. M3.5's D1 version found window=3 outperforming the M3 default
of 5 on every swept threshold/basis (IMPLEMENTATION.md known limitation
#6) -- worth knowing whether the M5 window (currently 12, the spec's L
default) shows a similar miscalibration.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics, metrics_by_direction

WINDOWS = (6, 12, 18)
THRESHOLDS = (0.75, 1.0, 1.5)


def run_rrs_sensitivity_m5(
    universe_m1: dict, universe_m5: dict, universe_d1: dict,
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None = None,
    base_config: BacktestConfigM5 | None = None,
) -> pd.DataFrame:
    base_config = base_config or BacktestConfigM5(shorts_enabled=True)
    earnings_blackout = earnings_blackout or {}

    rows = []
    for window in WINDOWS:
        for threshold in THRESHOLDS:
            cfg = replace(
                base_config,
                rrs_m5_window=window,
                rrs_m5_threshold_long=threshold,
                rrs_m5_threshold_short=-threshold,
            )
            result = run_m5_backtest(
                universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
                sectors, earnings_blackout, cfg,
            )
            trades = result.trades_df()
            trading_days = len(result.equity_curve) if result.equity_curve is not None else 0
            overall = compute_metrics(trades, result.equity_curve, trading_days)
            by_dir = metrics_by_direction(trades, base_config.starting_equity) if not trades.empty else {}

            row = {"window": window, "threshold": threshold}
            row.update({f"overall_{k}": v for k, v in overall.items()})
            for direction in ("LONG", "SHORT"):
                dm = by_dir.get(direction, {"n_trades": 0, "win_rate": None, "profit_factor": None, "total_pnl": 0.0})
                row.update({f"{direction.lower()}_{k}": v for k, v in dm.items()})
            rows.append(row)

    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_studies_m5.py -v`

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`

```bash
git add src/rs_spy/backtest/studies/rrs_sensitivity_m5.py tests/unit/test_studies_m5.py
git commit -m "Add M5-cadence RRS sensitivity sweep study (08 §3.3), long and short"
```

---

### Task 5: `backtest/studies/bias_confusion_m5.py` + `backtest/studies/time_of_day_m5.py` -- 08 §3.4/§3.5

**Files:**
- Create: `src/rs_spy/backtest/studies/bias_confusion_m5.py`
- Create: `src/rs_spy/backtest/studies/time_of_day_m5.py`
- Test: `tests/unit/test_studies_m5.py` (append)

**Interfaces:**
- `run_bias_confusion_m5(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5, horizon_bars=12,
  flat_threshold_pct=0.001) -> dict` with keys `"contingency"` (DataFrame) and `"hit_rates"`
  (dict) and `"n_bars"` (int). Needs no backtest run at all -- only `bias.engine.bias_series` and
  SPY's own M5 close prices.
- `run_time_of_day_regime_slice_m5(trades, regime_d1_m5) -> pd.DataFrame` -- `trades` is a
  `BacktestResultM5.trades_df()`-shaped DataFrame, `regime_d1_m5` is `PreparedM5.regime_d1_m5`
  from the SAME run that produced `trades` (Task 6's CLI passes its one shared baseline run's
  values into this function -- no extra backtest run needed).

These are two new studies with no D1-cadence precedent to mirror (M3.5 only built 08 §3.1-3.3).
Bundled into one task since both are small, self-contained, and cheap (no additional full
backtest runs, unlike Tasks 2 and 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_studies_m5.py`:

```python
from rs_spy.backtest.studies.bias_confusion_m5 import run_bias_confusion_m5
from rs_spy.backtest.studies.time_of_day_m5 import run_time_of_day_regime_slice_m5


def test_run_bias_confusion_m5_returns_contingency_table_and_hit_rates(small_universe):
    u = small_universe
    result = run_bias_confusion_m5(u["spy_m1"], u["spy_m5"], u["spy_d1"], u["qqq_m1"], u["qqq_m5"])
    assert "contingency" in result and "hit_rates" in result
    assert set(result["hit_rates"].keys()) == {"STRONG_BULL", "BULL", "STRONG_BEAR", "BEAR", "NEUTRAL"}
    for rate in result["hit_rates"].values():
        assert rate is None or 0.0 <= rate <= 1.0


def test_run_bias_confusion_m5_hit_rate_is_hand_computable_on_a_synthetic_uptrend():
    # A monotonically rising SPY series should show a high "hit rate" for BULL/STRONG_BULL
    # buckets predicting UP -- a loose but real sanity check the classification math is right
    # (not just structurally present).
    from rs_spy.data.resample import resample_ohlcv

    up_dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=15)]
    spy_m1 = _build_m1(up_dates, drift=0.01, seed=10)  # strong, steady uptrend
    qqq_m1 = _build_m1(up_dates, drift=0.01, seed=11)
    spy_m5, qqq_m5 = resample_ohlcv(spy_m1, "5min"), resample_ohlcv(qqq_m1, "5min")
    spy_d1, qqq_d1 = _build_d1(spy_m1), _build_d1(qqq_m1)

    result = run_bias_confusion_m5(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5, horizon_bars=6)
    bull_rate = result["hit_rates"]["BULL"]
    strong_bull_rate = result["hit_rates"]["STRONG_BULL"]
    # At least one bull-family bucket must have actually occurred and shown a majority-UP
    # hit rate in a steady uptrend -- if both are None, the bias engine never left NEUTRAL on
    # this fixture and the test's premise (a real uptrend) has failed, which is itself worth
    # surfacing as a test failure rather than silently passing.
    assert (bull_rate is not None and bull_rate > 0.5) or (strong_bull_rate is not None and strong_bull_rate > 0.5)


def test_run_time_of_day_regime_slice_m5_buckets_by_session_time_and_regime():
    trades = pd.DataFrame([
        {"symbol": "AAPL", "direction": "LONG", "entry_time": pd.Timestamp("2026-02-02 14:35:00", tz="UTC"), "pnl": 100.0},  # 09:35 ET -> OPEN
        {"symbol": "MSFT", "direction": "LONG", "entry_time": pd.Timestamp("2026-02-02 17:00:00", tz="UTC"), "pnl": -50.0},  # 12:00 ET -> MIDDAY
        {"symbol": "AMD", "direction": "SHORT", "entry_time": pd.Timestamp("2026-02-02 20:00:00", tz="UTC"), "pnl": 30.0},  # 15:00 ET -> CLOSE
    ])
    regime = pd.Series(
        ["TREND_UP"] * 3,
        index=pd.date_range("2026-02-02 14:30", periods=3, freq="5min", tz="UTC"),
    )
    # asof needs an index that actually spans the trade timestamps -- build a longer one.
    regime = pd.Series("CHOP", index=pd.date_range("2026-02-02 14:30", "2026-02-02 21:00", freq="5min", tz="UTC"))

    summary = run_time_of_day_regime_slice_m5(trades, regime)
    assert set(summary["time_of_day"]) == {"OPEN", "MIDDAY", "CLOSE"}
    assert (summary["regime"] == "CHOP").all()
    assert summary["n_trades"].sum() == 3


def test_run_time_of_day_regime_slice_m5_handles_empty_trades():
    empty = pd.DataFrame(columns=["symbol", "direction", "entry_time", "pnl"])
    summary = run_time_of_day_regime_slice_m5(empty, pd.Series(dtype=object))
    assert summary.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_studies_m5.py -k "bias_confusion or time_of_day" -v`
Expected: FAIL (`ModuleNotFoundError` for both new modules).

- [ ] **Step 3: Implement `bias_confusion_m5.py`**

```python
"""M7 bias-engine confusion matrix. algo-spec/08-backtesting-and-validation.md
§3.4. Not yet built at any cadence (M3.5 covered §3.1-3.3 only).

For every M5 bar with a resolved bias bucket (bias/engine.py's
BULL/STRONG_BULL/NEUTRAL/BEAR/STRONG_BEAR), classifies SPY's own forward
realized price direction over the following `horizon_bars` M5 bars as UP,
DOWN, or FLAT (a return within +-`flat_threshold_pct` of zero), and builds
a bucket x realized-direction contingency table plus a directional hit
rate (BULL/STRONG_BULL bars where realized was UP; BEAR/STRONG_BEAR bars
where realized was DOWN; NEUTRAL bars where realized was FLAT) -- the
natural cadence-agnostic way to ask "is the bias engine's call actually
predictive of what SPY does next." Needs no backtest run -- only the bias
engine's own output and SPY's M5 close series.
"""
import pandas as pd

from rs_spy.bias.buckets import BEAR, BULL, NEUTRAL, STRONG_BEAR, STRONG_BULL
from rs_spy.bias.engine import bias_series

UP = "UP"
DOWN = "DOWN"
FLAT = "FLAT"

DEFAULT_HORIZON_BARS = 12  # ~1 hour at M5 cadence
DEFAULT_FLAT_THRESHOLD_PCT = 0.001  # 0.1%


def _forward_direction(close: pd.Series, horizon_bars: int, flat_threshold_pct: float) -> pd.Series:
    forward_return = close.shift(-horizon_bars) / close - 1.0
    direction = pd.Series(FLAT, index=close.index, dtype=object)
    direction[forward_return > flat_threshold_pct] = UP
    direction[forward_return < -flat_threshold_pct] = DOWN
    direction[forward_return.isna()] = None
    return direction


def run_bias_confusion_m5(
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> dict:
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    direction = _forward_direction(spy_m5["close"], horizon_bars, flat_threshold_pct)

    df = pd.DataFrame({"bias": bias_df["bias"], "realized": direction}).dropna()
    bucket_order = [STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR]
    direction_order = [UP, FLAT, DOWN]

    contingency = (
        pd.crosstab(df["bias"], df["realized"])
        .reindex(index=bucket_order, columns=direction_order, fill_value=0)
        .reset_index()
    )

    hit_rates = {}
    for bucket in (STRONG_BULL, BULL):
        sub = df[df["bias"] == bucket]
        hit_rates[bucket] = float((sub["realized"] == UP).mean()) if not sub.empty else None
    for bucket in (STRONG_BEAR, BEAR):
        sub = df[df["bias"] == bucket]
        hit_rates[bucket] = float((sub["realized"] == DOWN).mean()) if not sub.empty else None
    sub = df[df["bias"] == NEUTRAL]
    hit_rates[NEUTRAL] = float((sub["realized"] == FLAT).mean()) if not sub.empty else None

    return {"contingency": contingency, "hit_rates": hit_rates, "n_bars": len(df)}
```

- [ ] **Step 4: Implement `time_of_day_m5.py`**

```python
"""M7 time-of-day / regime slicing. algo-spec/08-backtesting-and-validation.md
§3.5. Not yet built at any cadence.

Slices a real M5 backtest's realized trades by (a) entry time-of-day bucket
(OPEN 09:30-10:30 ET, MIDDAY 10:30-14:30 ET, CLOSE 14:30-15:55 ET -- the
session structure algo-spec 05/06/07 reference throughout) and (b) the D1
regime (bias/regime.py's TREND_UP/CHOP/TREND_DOWN) in effect at the entry
bar, reporting trade count / win rate / expectancy per bucket, separately
by direction (algo-spec 08 §3's "long and short reported separately").
Needs no additional backtest run -- takes an already-computed trade log and
regime series from the caller's own baseline run.
"""
import pandas as pd

OPEN = "OPEN"
MIDDAY = "MIDDAY"
CLOSE = "CLOSE"

_OPEN_END = pd.Timedelta(hours=10, minutes=30)
_MIDDAY_END = pd.Timedelta(hours=14, minutes=30)

SUMMARY_COLUMNS = ["direction", "time_of_day", "regime", "n_trades", "win_rate", "expectancy", "total_pnl"]


def _time_of_day_bucket(entry_time: pd.Timestamp) -> str:
    et = entry_time.tz_convert("America/New_York")
    tod = pd.Timedelta(hours=et.hour, minutes=et.minute, seconds=et.second)
    if tod < _OPEN_END:
        return OPEN
    if tod < _MIDDAY_END:
        return MIDDAY
    return CLOSE


def run_time_of_day_regime_slice_m5(trades: pd.DataFrame, regime_d1_m5: pd.Series) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    df = trades.copy()
    df["time_of_day"] = df["entry_time"].apply(_time_of_day_bucket)
    df["regime"] = df["entry_time"].apply(lambda t: regime_d1_m5.asof(t))

    return (
        df.groupby(["direction", "time_of_day", "regime"])
        .agg(n_trades=("pnl", "size"), win_rate=("pnl", lambda s: (s > 0).mean()),
             expectancy=("pnl", "mean"), total_pnl=("pnl", "sum"))
        .reset_index()
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_studies_m5.py -v`

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`

```bash
git add src/rs_spy/backtest/studies/bias_confusion_m5.py src/rs_spy/backtest/studies/time_of_day_m5.py tests/unit/test_studies_m5.py
git commit -m "Add M5 bias-confusion-matrix (08 §3.4) and time-of-day/regime-slicing (08 §3.5) studies"
```

---

### Task 6: `scripts/run_validation_studies.py` -- CLI wiring all 5 studies

**Files:**
- Create: `scripts/run_validation_studies.py`
- Test: `tests/integration/test_run_validation_studies_script.py`

**Interfaces:**
- Consumes: every study module from Tasks 2-5, `engine_m5._prepare_m5`/`run_m5_backtest`/
  `BacktestConfigM5`, `config.get_settings`, `data.loader.{load_universe_daily_bars,
  load_universe_m1_bars,load_universe_m5_bars}`, `data.warehouse.connect`,
  `universe.{load_earnings_blackout,load_universe}` -- same loader/config pattern as
  `scripts/run_backtest_intraday.py` and `scripts/run_validation_studies_m35.py`.

Read `scripts/run_validation_studies_m35.py` (the D1 precedent's CLI shape) and
`scripts/run_backtest_intraday.py` (the M5 loader-wiring precedent) in full first.

- [ ] **Step 1: Write the failing integration test**

This script's real execution is far too slow for the test suite (see Global Constraints).
Following this project's existing pattern for `test_run_backtest_intraday_script.py` (noted in
IMPLEMENTATION.md's known limitation #22 as re-implementing the script's wiring inline rather
than invoking a slow real `main()`), write a test that mocks the warehouse/loader calls with a
tiny synthetic universe and asserts the script's `main()` runs end-to-end and writes the expected
CSV files -- do not run it against real cached data.

Create `tests/integration/test_run_validation_studies_script.py`:

```python
from unittest.mock import patch

import pandas as pd
import typer.testing

import scripts.run_validation_studies as script


def _m1_session(date, n_minutes, start_price, drift, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{date} 09:30", periods=n_minutes, freq="1min", tz="America/New_York").tz_convert("UTC")
    noise = rng.normal(0, 0.05, n_minutes)
    close = start_price + np.cumsum(np.full(n_minutes, drift) + noise)
    high = close + abs(rng.normal(0.05, 0.02, n_minutes))
    low = close - abs(rng.normal(0.05, 0.02, n_minutes))
    open_ = close - drift - noise
    volume = rng.integers(500, 1500, n_minutes).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_main_runs_end_to_end_and_writes_expected_reports(tmp_path, monkeypatch):
    from rs_spy.data.resample import resample_ohlcv

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=15)]
    spy_m1 = pd.concat([_m1_session(d, 390, 100 + i, 0.0005, 1 + i) for i, d in enumerate(dates)])
    qqq_m1 = pd.concat([_m1_session(d, 390, 200 + i, 0.0006, 20 + i) for i, d in enumerate(dates)])
    aapl_m1 = pd.concat([_m1_session(d, 390, 150 + i, 0.0008, 40 + i) for i, d in enumerate(dates)])
    spy_m5, qqq_m5, aapl_m5 = resample_ohlcv(spy_m1, "5min"), resample_ohlcv(qqq_m1, "5min"), resample_ohlcv(aapl_m1, "5min")

    def _d1(m1):
        d = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        d.index = d.index.tz_localize(None)
        return d

    all_m1 = {"SPY": spy_m1, "QQQ": qqq_m1, "AAPL": aapl_m1}
    all_m5 = {"SPY": spy_m5, "QQQ": qqq_m5, "AAPL": aapl_m5}
    all_d1 = {"SPY": _d1(spy_m1), "QQQ": _d1(qqq_m1), "AAPL": _d1(aapl_m1)}

    class FakeUniverseEntry:
        def __init__(self, symbol, sector):
            self.symbol, self.sector = symbol, sector

    class FakeUniverse:
        primary_benchmark, secondary_benchmark = "SPY", "QQQ"
        trade_symbols = ["AAPL"]
        universe = [FakeUniverseEntry("AAPL", "Technology")]

    monkeypatch.setattr(script, "load_universe", lambda *_: FakeUniverse())
    monkeypatch.setattr(script, "load_earnings_blackout", lambda *_: {})
    monkeypatch.setattr(script, "connect", lambda *_: object())
    monkeypatch.setattr(script, "load_universe_m1_bars", lambda con, syms: all_m1)
    monkeypatch.setattr(script, "load_universe_m5_bars", lambda con, syms: all_m5)
    monkeypatch.setattr(script, "load_universe_daily_bars", lambda con, syms: all_d1)

    settings = script.get_settings()
    monkeypatch.setattr(settings, "reports_dir", tmp_path)
    monkeypatch.setattr(script, "get_settings", lambda: settings)

    runner = typer.testing.CliRunner()
    result = runner.invoke(script.app, [])
    assert result.exit_code == 0, result.output

    out_dir = tmp_path / "m7_studies"
    for name in (
        "baseline_trades.csv", "ablation_trades.csv", "ablation_summary_long.csv",
        "ablation_summary_short.csv", "walk_away_signals.csv", "rrs_sensitivity.csv",
        "bias_confusion.csv", "time_of_day_regime.csv",
    ):
        assert (out_dir / name).exists(), f"missing {name}"
```

Check `settings.reports_dir`'s real type in `src/rs_spy/config.py` before writing the
`monkeypatch.setattr(settings, "reports_dir", tmp_path)` line -- if `get_settings()` is cached
(e.g. `@lru_cache`), you may need `settings.reports_dir = tmp_path` via a mutable settings object
instead, or clear the cache; read the actual current implementation and adapt.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_run_validation_studies_script.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'scripts.run_validation_studies'`).

- [ ] **Step 3: Implement**

```python
"""M7: full validation study suite (algo-spec 08 §3), M5 cadence. Adds an M5
sibling to scripts/run_validation_studies_m35.py's D1-cadence suite (kept
unchanged -- that precedent is documented in IMPLEMENTATION.md's M3.5
section) -- the same relationship run_backtest_intraday.py has to
run_backtest_d1.py.

**Runtime**: this is SLOW. One shared baseline backtest + 6 gate-ablation
re-runs + 9 RRS-sensitivity re-runs = ~16 full run_m5_backtest invocations,
each on the order of 15-20 minutes for the full curated universe (the M5
precompute layer's non-vectorized indicator loops dominate -- see this
repo's README for the identical note on run_backtest_intraday.py). Expect
several hours for the full suite. The bias-confusion (§3.4) and
time-of-day/regime (§3.5) studies are comparatively instant (no extra
backtest runs).
"""
import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5, run_m5_backtest
from rs_spy.backtest.studies.ablation_m5 import run_gate_ablation_m5
from rs_spy.backtest.studies.bias_confusion_m5 import run_bias_confusion_m5
from rs_spy.backtest.studies.rrs_sensitivity_m5 import run_rrs_sensitivity_m5
from rs_spy.backtest.studies.time_of_day_m5 import run_time_of_day_regime_slice_m5
from rs_spy.backtest.studies.walk_away_m5 import run_walk_away_m5
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(horizon_bars_walk_away: int = 78, horizon_bars_bias: int = 12) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    load_syms = list(dict.fromkeys([spy, qqq, *trade_symbols]))

    typer.echo(f"Loading real cached data for {len(load_syms)} symbols (full window)...")
    all_m1 = load_universe_m1_bars(con, load_syms)
    all_m5 = load_universe_m5_bars(con, load_syms)
    all_d1 = load_universe_daily_bars(con, load_syms)

    trade_m1 = {s: all_m1[s] for s in trade_symbols}
    trade_m5 = {s: all_m5[s] for s in trade_symbols}
    trade_d1 = {s: all_d1[s] for s in trade_symbols}
    sectors = {s.symbol: s.sector for s in universe.universe}

    base_config = BacktestConfigM5(shorts_enabled=True)
    out_dir = settings.reports_dir / "m7_studies"
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo("\n=== Baseline M5 backtest (shared by walk-away, ablation scoring, time-of-day) ===")
    baseline_prepared = _prepare_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
    )
    baseline_result = run_m5_backtest(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
    )
    baseline_trades = baseline_result.trades_df()
    typer.echo(f"Baseline trades: {len(baseline_result.trades)}")
    baseline_trades.to_csv(out_dir / "baseline_trades.csv", index=False)

    typer.echo("\n=== 3.1 Gate ablation (M5, 6 additional runs) ===")
    ablation = run_gate_ablation_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
        baseline_prepared, baseline_result,
    )
    typer.echo(f"Trades per run: {ablation['run_trade_counts']}")
    typer.echo("LONG summary:\n" + ablation["summary_long"].to_string(index=False))
    typer.echo("SHORT summary:\n" + ablation["summary_short"].to_string(index=False))
    ablation["trades"].to_csv(out_dir / "ablation_trades.csv", index=False)
    ablation["summary_long"].to_csv(out_dir / "ablation_summary_long.csv", index=False)
    ablation["summary_short"].to_csv(out_dir / "ablation_summary_short.csv", index=False)

    typer.echo("\n=== 3.2 Walk-away analysis (M5, reuses baseline run) ===")
    walk_away = run_walk_away_m5(baseline_prepared, baseline_trades, base_config, horizon_bars=horizon_bars_walk_away)
    signals = walk_away["signals"]
    typer.echo(f"Entry signals (IDLE->QUALIFIED): {len(signals)}")
    if not signals.empty:
        for direction in ("LONG", "SHORT"):
            sub = signals[signals["direction"] == direction]
            if sub.empty:
                continue
            typer.echo(f"  {direction} MFE (R): mean={sub['mfe_r'].mean():.2f} median={sub['mfe_r'].median():.2f}")
            typer.echo(f"  {direction} MAE (R): mean={sub['mae_r'].mean():.2f} median={sub['mae_r'].median():.2f}")
    if not baseline_trades.empty:
        typer.echo(
            f"Realized trade R: mean={baseline_trades['r_multiple'].mean():.2f} "
            f"median={baseline_trades['r_multiple'].median():.2f}"
        )
    signals.to_csv(out_dir / "walk_away_signals.csv", index=False)

    typer.echo("\n=== 3.3 RRS sensitivity sweep (M5, 9 runs) ===")
    sweep = run_rrs_sensitivity_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
    )
    typer.echo(sweep.to_string(index=False))
    sweep.to_csv(out_dir / "rrs_sensitivity.csv", index=False)

    typer.echo("\n=== 3.4 Bias-engine confusion matrix ===")
    confusion = run_bias_confusion_m5(
        all_m1[spy], all_m5[spy], all_d1[spy], all_m1[qqq], all_m5[qqq],
        horizon_bars=horizon_bars_bias,
    )
    typer.echo(confusion["contingency"].to_string(index=False))
    typer.echo(f"Hit rates: {confusion['hit_rates']}")
    confusion["contingency"].to_csv(out_dir / "bias_confusion.csv", index=False)

    typer.echo("\n=== 3.5 Time-of-day / regime slicing ===")
    tod = run_time_of_day_regime_slice_m5(baseline_trades, baseline_prepared.regime_d1_m5)
    typer.echo(tod.to_string(index=False))
    tod.to_csv(out_dir / "time_of_day_regime.csv", index=False)

    typer.echo(f"\nWrote all study outputs to {out_dir}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_run_validation_studies_script.py -v`
Expected: PASS. This tiny synthetic run should take seconds, not minutes -- if it takes anywhere
close to a minute, something is wrong with the test's fixture size (too many days/bars), not with
the production code; shrink the fixture rather than accepting a slow test.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q && ruff check .`

```bash
git add scripts/run_validation_studies.py tests/integration/test_run_validation_studies_script.py
git commit -m "Add scripts/run_validation_studies.py: CLI wiring all 5 M7 studies"
```

---

### Task 7: Real run against the full warehouse + IMPLEMENTATION.md documentation + final review

**Files:**
- Modify: `IMPLEMENTATION.md`

This is not a subagent-dispatched implementation task -- it's the controller's own final step,
mirroring M6's Task 8. Before running the real suite, tell the user plainly that this is a
multi-hour real-data run (per this plan's Global Constraints) and let them decide whether to run
it now, overnight, or skip straight to documenting the architecture without a fresh real-data
result (the code and tests from Tasks 1-6 are already fully validated by that point regardless).

If proceeding with the real run:

- [ ] Run `python scripts/run_validation_studies.py` against the real warehouse (in the
  background, given the multi-hour runtime -- do not block the session on it).
- [ ] Once complete, read `reports/m7_studies/*.csv` and the printed summaries directly (do not
  trust a report of "it ran" without inspecting actual numbers -- this project's established
  practice throughout M3.5/M5/M6).
- [ ] Add a "## M7: full validation study suite" section to `IMPLEMENTATION.md` (after the
  existing "## M7 pre-work..." section) documenting: what was built (all 5 study modules + CLI),
  real bugs found and fixed during the build (if any), and the real results from each of the 5
  studies -- reported honestly, explicitly flagged as directional given the small (~3-trade)
  sample, matching M3.5's own precedent for its 8-trade D1 sample.
- [ ] Update the "## Milestone tracker" section's M7 entry from "in progress" to "complete."
- [ ] Dispatch the final whole-branch review (most capable available model) across this plan's
  full diff range (`git merge-base` back to the M7 pre-work's last commit, `d49ef86`), per
  superpowers:subagent-driven-development's process. Fold its findings into IMPLEMENTATION.md the
  same way M6's final review was folded in (a dedicated paragraph, known-limitation items for
  anything real but non-blocking).
- [ ] Commit.
