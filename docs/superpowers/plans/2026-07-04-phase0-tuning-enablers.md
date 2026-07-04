# M7.5 Phase 0: Tuning-Campaign Enablers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the five enablers from `docs/tuning/m7.5-tuning-matrix.md` §3 Phase 0 — a stop-multiplier config knob, trigger-bypass funnel instrumentation, the known-limitation-#23 dip-arm NaN fix, a `prepared=` reuse parameter on `run_m5_backtest`, and the D1 trigger forward-return study — so the tuning rounds (1–4) can run measurably and cheaply.

**Architecture:** All changes are additive/behavior-preserving against the existing two-phase M5 engine (`_prepare_m5` precompute → `run_m5_backtest` event loop). The funnel is a flat dict of counters incremented inline in the event loop and returned on `BacktestResultM5`. The trigger study is a new standalone study module + script following `backtest/studies/bias_confusion_m5.py`'s exact pattern (pure-series helper, thin wrapper calling `bias_series`, no backtest run needed).

**Tech Stack:** Python 3.14, pandas, pytest (hermetic, no network), typer CLI scripts, ruff.

## Global Constraints

- Every default-config code path must behave **bit-for-bit identically** after each task (the funnel restructure and the `prepared=` parameter are refactors + additions, not behavior changes). The existing 204-test suite passing unchanged is the check.
- Native-first-reindex-last convention: per-symbol quantities are computed on the symbol's own native M5 index first, reindexed onto the master calendar last (see `engine_m5.py`'s module docstring). Task 2's fix exists precisely to bring the one violator into line.
- All tests hermetic: no network, no credentials, no warehouse reads.
- `python -m pytest -q` green and `ruff check .` clean before every commit.
- Run tests from the repo root with the venv active: `source .venv/bin/activate`.
- Commit messages prefixed `M7.5 Phase 0:`.
- Documented-not-silent norm: every deliberate simplification goes in a docstring or `IMPLEMENTATION.md`, never left implicit.

---

### Task 1: `stop_atr_mult` config knob (matrix 0a)

**Files:**
- Modify: `src/rs_spy/algo/risk.py:56-62` (`stop_price_long`/`stop_price_short`)
- Modify: `src/rs_spy/backtest/engine_m5.py:46-69` (`BacktestConfigM5`), `engine_m5.py:551` and `engine_m5.py:590` (the two `risk.stop_price_*` call sites)
- Test: `tests/unit/test_risk.py`, `tests/unit/test_engine_m5_backtest.py`

**Interfaces:**
- Produces: `risk.stop_price_long(entry: float, atr_m5: float, stop_atr_mult: float = 1.0) -> float` and the mirrored `stop_price_short`; `BacktestConfigM5.stop_atr_mult: float = 1.0`. Round 3 of the tuning campaign sweeps this knob; Task 4's `prepared=` parameter makes that sweep cheap (stops are event-loop-only, no re-prepare needed).

Background: today `stop_price_long` returns `entry - min(STOP_ATR_MULT, STOP_ATR_CAP_MULT) * atr_m5`. The `min()` is a documented no-op (1.0 < 1.5). Once the multiplier becomes a knob, that `min()` would turn into an **active silent clamp** (a swept value of 2.0 would silently become 1.5) — so the knob replaces the `min()` entirely. `STOP_ATR_CAP_MULT` stays defined: it belongs to the spec's swing-low stop variant (matrix cell C2, future work), and the module docstring is updated to say so.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_risk.py`:

```python
def test_stop_price_long_and_short_accept_a_stop_atr_mult():
    # matrix cell C1: the ATR-stop multiplier is a tunable knob, default 1.0
    assert stop_price_long(entry=100.0, atr_m5=2.0, stop_atr_mult=1.5) == pytest.approx(97.0)
    assert stop_price_short(entry=100.0, atr_m5=2.0, stop_atr_mult=1.5) == pytest.approx(103.0)
    # a value above the (swing-low-variant-only) 1.5 cap must NOT be clamped
    assert stop_price_long(entry=100.0, atr_m5=2.0, stop_atr_mult=2.0) == pytest.approx(96.0)
    assert stop_price_short(entry=100.0, atr_m5=2.0, stop_atr_mult=2.0) == pytest.approx(104.0)
    # default is unchanged
    assert stop_price_long(entry=100.0, atr_m5=2.0) == pytest.approx(98.0)
```

Append to `tests/unit/test_engine_m5_backtest.py` (follows the existing
`test_prepare_m5_threads_rrs_thresholds_into_long_gate_call` wraps-spy pattern; `patch` is
already imported at the top of the file):

```python
def test_run_m5_backtest_threads_stop_atr_mult_into_stop_price_calls(monkeypatch):
    """BacktestConfigM5.stop_atr_mult must reach risk.stop_price_long as the
    stop_atr_mult argument -- a config knob that is accepted but never threaded
    would silently run every sweep cell at the 1.0 default."""
    from rs_spy.algo import risk as risk_module

    sym = "KNOB"
    n = 8
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    closes = [100.0] * n
    bars_df = pd.DataFrame(
        {
            "open": [c - 0.02 for c in closes],
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "volume": [1_000.0] * n,
        },
        index=calendar,
    )
    rrs_vals = [-1.0, 1.0] + [1.0] * (n - 2)  # crosses up at bar 1 -> arms the dip
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    with patch.object(engine_m5.risk, "stop_price_long", wraps=risk_module.stop_price_long) as spy:
        run_m5_backtest(
            universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
            spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
            qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
            sectors={sym: "Technology"},
            config=BacktestConfigM5(stop_atr_mult=2.0),
        )
    assert spy.called, "expected at least one long stop-price computation"
    assert all(call.kwargs.get("stop_atr_mult") == 2.0 for call in spy.call_args_list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_risk.py::test_stop_price_long_and_short_accept_a_stop_atr_mult tests/unit/test_engine_m5_backtest.py::test_run_m5_backtest_threads_stop_atr_mult_into_stop_price_calls -v`
Expected: both FAIL — `TypeError: stop_price_long() got an unexpected keyword argument 'stop_atr_mult'` (first), and `TypeError: BacktestConfigM5.__init__() got an unexpected keyword argument 'stop_atr_mult'` (second).

- [ ] **Step 3: Implement**

In `src/rs_spy/algo/risk.py`, replace the two stop functions:

```python
def stop_price_long(entry: float, atr_m5: float, stop_atr_mult: float = STOP_ATR_MULT) -> float:
    return entry - stop_atr_mult * atr_m5


def stop_price_short(entry: float, atr_m5: float, stop_atr_mult: float = STOP_ATR_MULT) -> float:
    return entry + stop_atr_mult * atr_m5
```

Update the `STOP_ATR_CAP_MULT` comment on line 38 from
`# documented no-op under the ATR-only simplification above` to
`# reserved for the spec's swing-low stop variant (07 §3 cap); not applied to the ATR-only stop -- a swept stop_atr_mult must never be silently clamped`.

In `src/rs_spy/backtest/engine_m5.py`, add to `BacktestConfigM5` (after `unfilled_cancel_bars: int = 2`):

```python
    stop_atr_mult: float = 1.0
```

Change line 551 from `stop = risk.stop_price_long(bar["close"], atr)` to:

```python
                stop = risk.stop_price_long(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
```

Change line 590 from `stop = risk.stop_price_short(bar["close"], atr)` to:

```python
                stop = risk.stop_price_short(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
```

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `python -m pytest tests/unit/test_risk.py tests/unit/test_engine_m5_backtest.py -q` then `python -m pytest -q && ruff check .`
Expected: all PASS, lint clean (default behavior unchanged — every existing test green).

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/algo/risk.py src/rs_spy/backtest/engine_m5.py tests/unit/test_risk.py tests/unit/test_engine_m5_backtest.py
git commit -m "M7.5 Phase 0: promote stop ATR multiplier to a BacktestConfigM5 knob"
```

---

### Task 2: Fix known-limitation #23 — dip-arm cross uses native previous reading (matrix 0c)

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py:477-501` (step-3 watchlist block) plus a small precompute before the event loop
- Modify: `IMPLEMENTATION.md` known-limitations item 23 (mark resolved)
- Test: `tests/unit/test_engine_m5_backtest.py`

**Interfaces:**
- Consumes: `prepared.features[sym]["rolling_rrs_m5"]` / `["lrsi_m5"]` (reindexed onto the master calendar, NaN at bars the symbol has no native bar for).
- Produces: no API change. `rrs_prev`/`lrsi_prev` fed to `watchlist.next_state_long/_short` become the symbol's **last real native reading** instead of "whatever sat on the previous master-calendar row".

Background: `run_m5_backtest` reads `rrs_prev = prepared.features[sym]["rolling_rrs_m5"].iat[i - 1]` on the **reindexed** frame — the one entry/exit-signal series in the loop violating the native-first convention (IMPLEMENTATION.md item #23). For a thin/gappy symbol whose preceding master bar has no native data, `rrs_prev` reads NaN and the crossing comparison silently evaluates False, suppressing dip-arm advancement for exactly the thin names a universe expansion would add. The fix: `ffill().shift(1)` on the reindexed series reproduces the native `shift(1)` at every bar the symbol actually trades (forward-fill carries the last real reading across gap rows; the shift moves it strictly before the current bar), with NaN before the first real reading (same "no cross" behavior as today's `None` at `i == 0`).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_engine_m5_backtest.py`:

```python
def test_dip_arm_cross_uses_symbols_last_native_reading_across_a_gap_bar(monkeypatch):
    """Regression test for IMPLEMENTATION.md known-limitation #23: the dip-arm
    RRS/LRSI cross detection read its "previous" value from the immediately
    preceding MASTER-calendar row of the reindexed features frame. For a
    thin/gappy symbol (a real, anticipated case on the IEX-only feed) that row
    is NaN whenever the symbol had no native bar there, so a genuine
    dip-and-recover (RRS -1.0 -> [gap] -> +1.0) never armed: NaN < 0 is False.

    The gate series here is hand-held True through the gap bar (production
    gates read False on gap bars, which ALSO demotes the symbol -- that is the
    matrix's Round-1 alert-model redesign scope, deliberately not this fix).
    This test pins cross-detection in isolation via the hand-built seam: with
    the fix, prev at the post-gap bar is the symbol's last REAL reading (-1.0),
    the cross fires, and a trade results; without it, the symbol sits QUALIFIED
    forever and the trade log is empty."""
    sym = "THIN"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")

    closes = [100.0] * 6 + [90.0] * (n - 6)  # crash at bar 6 forces a clean hard-stop exit
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    lows[6] = 89.0  # intrabar drop through the stop
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )

    # bar 0: -1.0 (real reading); bar 1: NaN (the symbol has no native bar --
    # a master-calendar gap row); bar 2: +1.0 (real reading again).
    rrs_vals = [-1.0, np.nan, 1.0] + [1.0] * (n - 3)

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    assert not trades_df.empty, (
        "RRS crossed up over a gap bar (-1.0 -> NaN gap -> +1.0) but no trade resulted -- "
        "the dip-arm 'previous' value must be the symbol's last real native reading, not the NaN gap row"
    )
    assert trades_df.iloc[0]["direction"] == "LONG"
    assert trades_df.iloc[0]["exit_reason"] == "hard_stop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py::test_dip_arm_cross_uses_symbols_last_native_reading_across_a_gap_bar -v`
Expected: FAIL on the `not trades_df.empty` assertion (the cross at bar 2 is suppressed by the NaN prev).

- [ ] **Step 3: Implement**

In `src/rs_spy/backtest/engine_m5.py`, immediately after the `in_entry_window = ...` line (line 317), add:

```python
    # Cross-detection "previous" values (dip-arm RRS/LRSI crossings) must be
    # each symbol's own last REAL reading, not whatever sat on the immediately
    # preceding master-calendar row -- which is NaN for thin/gappy symbols and
    # silently suppressed the crossing (IMPLEMENTATION.md known-limitation #23,
    # fixed here). ffill().shift(1) on the reindexed frame reproduces the
    # native shift(1) at every bar the symbol actually trades: the forward-fill
    # carries the last real reading across gap rows, and the shift moves it
    # strictly before the current bar. Bars the symbol has no native data for
    # still can't arm (rrs_now itself is NaN there, and NaN comparisons are
    # False), matching the strict-reindex "no signal" convention.
    rrs_prev_by_sym = {sym: prepared.features[sym]["rolling_rrs_m5"].ffill().shift(1) for sym in universe_m5}
    lrsi_prev_by_sym = {sym: prepared.features[sym]["lrsi_m5"].ffill().shift(1) for sym in universe_m5}
```

In the step-3 watchlist block, replace:

```python
            rrs_now = prepared.features[sym]["rolling_rrs_m5"].iat[i]
            rrs_prev = prepared.features[sym]["rolling_rrs_m5"].iat[i - 1] if i > 0 else None
            lrsi_now = prepared.features[sym]["lrsi_m5"].iat[i]
            lrsi_prev = prepared.features[sym]["lrsi_m5"].iat[i - 1] if i > 0 else None
```

with:

```python
            rrs_now = prepared.features[sym]["rolling_rrs_m5"].iat[i]
            rrs_prev = rrs_prev_by_sym[sym].iat[i]
            lrsi_now = prepared.features[sym]["lrsi_m5"].iat[i]
            lrsi_prev = lrsi_prev_by_sym[sym].iat[i]
```

(At `i == 0` the value is NaN instead of the old `None`; `watchlist.next_state_long`'s
`rrs_prev is not None and ... rrs_prev < 0` evaluates False either way — no behavior change.)

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -q` then `python -m pytest -q && ruff check .`
Expected: new test PASSES; every pre-existing test still green (dense-fixture behavior is identical: with no gaps, `ffill().shift(1).iat[i]` equals `.iat[i-1]`).

- [ ] **Step 5: Update IMPLEMENTATION.md**

In `IMPLEMENTATION.md`, edit known-limitations item 23: wrap the opening clause in `~~...~~` strikethrough and append:

```
**RESOLVED (M7.5 Phase 0).** `run_m5_backtest` now precomputes per-symbol
`ffill().shift(1)` "previous" series for the dip-arm RRS/LRSI crossings, so the
comparison uses the symbol's own last real native reading across master-calendar
gap rows. Regression test:
`test_dip_arm_cross_uses_symbols_last_native_reading_across_a_gap_bar`.
```

- [ ] **Step 6: Commit**

```bash
git add src/rs_spy/backtest/engine_m5.py tests/unit/test_engine_m5_backtest.py IMPLEMENTATION.md
git commit -m "M7.5 Phase 0: fix known-limitation #23 dip-arm cross NaN suppression on gap bars"
```

---

### Task 3: Trigger-bypass funnel instrumentation (matrix 0b / D3)

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py` (`BacktestResultM5`, `run_m5_backtest` steps 1, 3, 4)
- Modify: `scripts/run_backtest_intraday.py` (print funnel, write `funnel.json`, compute `same_bar_stop_rate`)
- Test: `tests/unit/test_engine_m5_backtest.py` (also extends `_build_prepared_for_run_loop` with a `confirm_trigger_long_by_symbol` parameter)

**Interfaces:**
- Consumes: Task 2's `rrs_prev_by_sym`/`lrsi_prev_by_sym` (this task's step-3 snippet is written against the post-Task-2 file).
- Produces: `BacktestResultM5.funnel: dict` — flat `{"<side>_<counter>": int}` with sides `long`/`short` and counters: `qualified_signals`, `dip_armed`, `entry_eval_via_dip`, `trigger_bars`, `trigger_coincidences`, `trigger_killed_by_bias_hold`, `trigger_bypass`, `eval_blocked_no_entry_window`, `eval_blocked_risk_halt`, `eval_blocked_bias`, `eval_killed_by_lockout_or_cap`, `eval_killed_by_quality`, `eval_killed_by_ranking`, `eval_killed_by_slots`, `eval_killed_by_sizing`, `orders_submitted`, `orders_filled`, `orders_cancelled_unfilled`. The CLI writes `reports/m5_backtest/funnel.json` containing the funnel plus `same_bar_stop_rate`. The tuning ledger's `n_qualified`/`n_dip_armed`/`n_trigger_coincidences` columns read from these.

Semantics (document in the `run_m5_backtest` docstring): counters are event counts over the whole run — `qualified_signals` counts `IDLE -> QUALIFIED` transitions, `trigger_coincidences` counts (trigger bar × QUALIFIED symbol) pairs **before** the bias-hold check, `eval_blocked_*` count ENTRY_EVAL symbol-bars turned away by that bar-level condition (a symbol re-arming later is counted again — that is the point: the funnel measures opportunities, not unique symbols). Short-side trigger/eval counters only accumulate when `shorts_enabled=True` (the short book's code paths are guarded by it, matching existing structure).

- [ ] **Step 1: Extend the test helper**

In `tests/unit/test_engine_m5_backtest.py`, add a `confirm_trigger_long_by_symbol=None` keyword to `_build_prepared_for_run_loop` (after `rs_failure_short_by_symbol=None`), add `confirm_trigger_long_by_symbol = confirm_trigger_long_by_symbol or {}` alongside the other defaults, and change the per-symbol line
`confirm_trigger_long[sym] = _flat_series(calendar, False)` to
`confirm_trigger_long[sym] = confirm_trigger_long_by_symbol.get(sym, _flat_series(calendar, False))`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_engine_m5_backtest.py`:

```python
def _funnel_scenario_bars(n, calendar):
    closes = [100.0] * 6 + [90.0] * (n - 6)
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    lows[6] = 89.0
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )


def test_funnel_counts_the_trigger_bypass_path_end_to_end(monkeypatch):
    """A QUALIFIED symbol on a LONG_TRIGGER bar with bias held 2+ bars must
    show up at every funnel stage: coincidence -> bypass -> submitted -> filled."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TRIG"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},  # never dips -> Path B can never fire; only the bypass can
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        confirm_trigger_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    f = result.funnel
    assert f["long_qualified_signals"] == 1  # IDLE -> QUALIFIED once, at bar 0
    assert f["long_dip_armed"] == 0
    assert f["long_trigger_bars"] == 1
    assert f["long_trigger_coincidences"] == 1
    assert f["long_trigger_killed_by_bias_hold"] == 0
    assert f["long_trigger_bypass"] == 1
    assert f["long_orders_submitted"] == 1
    assert f["long_orders_filled"] == 1
    assert not result.trades_df().empty


def test_funnel_counts_a_trigger_coincidence_killed_by_the_bias_two_bar_hold(monkeypatch):
    """Matrix thesis #3: a fresh trigger firing on the FIRST bullish bar fails
    bias_ok_long's 2-consecutive-bar hold. The funnel must record the
    coincidence AND attribute the kill to the bias hold."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TRIG"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    bias_by_bar = [BEAR, BEAR, BEAR] + [BULL] * (n - 3)
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER  # first BULL bar: family holds only 1 bar -> bias_ok_long is False

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=bias_by_bar,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        confirm_trigger_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    f = result.funnel
    assert f["long_trigger_bars"] == 1
    assert f["long_trigger_coincidences"] == 1
    assert f["long_trigger_killed_by_bias_hold"] == 1
    assert f["long_trigger_bypass"] == 0
    assert f["long_orders_submitted"] == 0
    assert result.trades_df().empty


def test_funnel_is_present_and_all_zero_when_nothing_ever_qualifies(monkeypatch):
    sym = "DEAD"
    n = 6
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, False)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    expected_keys = {
        f"{side}_{key}"
        for side in ("long", "short")
        for key in (
            "qualified_signals", "dip_armed", "entry_eval_via_dip",
            "trigger_bars", "trigger_coincidences", "trigger_killed_by_bias_hold", "trigger_bypass",
            "eval_blocked_no_entry_window", "eval_blocked_risk_halt", "eval_blocked_bias",
            "eval_killed_by_lockout_or_cap", "eval_killed_by_quality", "eval_killed_by_ranking",
            "eval_killed_by_slots", "eval_killed_by_sizing",
            "orders_submitted", "orders_filled", "orders_cancelled_unfilled",
        )
    }
    assert set(result.funnel) == expected_keys
    assert all(v == 0 for v in result.funnel.values())
```

`NO_TRIGGER` is already imported at the top of the file.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -k funnel -v`
Expected: 3 FAILURES — `AttributeError: 'BacktestResultM5' object has no attribute 'funnel'`.

- [ ] **Step 4: Implement in `engine_m5.py`**

Add `funnel: dict = field(default_factory=dict)` to `BacktestResultM5` (after `equity_curve`).

In `run_m5_backtest`, right before `risk_mgr = risk.RiskManager(...)`, add:

```python
    # Entry-funnel instrumentation (tuning-matrix cell D3): flat event counters
    # over the whole run. qualified_signals = IDLE->QUALIFIED transitions;
    # trigger_coincidences = (trigger bar x QUALIFIED symbol) pairs counted
    # BEFORE the bias 2-bar-hold check; eval_blocked_* = ENTRY_EVAL symbol-bars
    # turned away by that bar-level condition (re-arming counts again -- the
    # funnel measures opportunities, not unique symbols). Short-side trigger/
    # eval counters only accumulate when shorts_enabled=True.
    funnel = {
        f"{side}_{key}": 0
        for side in ("long", "short")
        for key in (
            "qualified_signals", "dip_armed", "entry_eval_via_dip",
            "trigger_bars", "trigger_coincidences", "trigger_killed_by_bias_hold", "trigger_bypass",
            "eval_blocked_no_entry_window", "eval_blocked_risk_halt", "eval_blocked_bias",
            "eval_killed_by_lockout_or_cap", "eval_killed_by_quality", "eval_killed_by_ranking",
            "eval_killed_by_slots", "eval_killed_by_sizing",
            "orders_submitted", "orders_filled", "orders_cancelled_unfilled",
        )
    }
```

**Step 1 of the loop (fills):** inside the `if fill is not None:` block, immediately after the `positions[sym] = PositionM5(...)` assignment, insert:

```python
                    funnel[("long_" if order["direction"] == LONG else "short_") + "orders_filled"] += 1
```

In the cancel branch, change:

```python
            if order["bars_waited"] >= config.unfilled_cancel_bars:
                del pending[sym]
```

to:

```python
            if order["bars_waited"] >= config.unfilled_cancel_bars:
                funnel[("long_" if order["direction"] == LONG else "short_") + "orders_cancelled_unfilled"] += 1
                del pending[sym]
```

**Step 3 of the loop (watchlist transitions):** replace the existing transition bookkeeping

```python
            if prev_state == watchlist.QUALIFIED and state_long[sym] == watchlist.DIP_ARMED:
                entry_path_long[sym] = "B"
            elif prev_state == watchlist.DIP_ARMED and state_long[sym] == watchlist.ENTRY_EVAL:
                pass  # entry_path_long[sym] already "B" from the prior bar
```

with:

```python
            if prev_state == watchlist.IDLE and state_long[sym] == watchlist.QUALIFIED:
                funnel["long_qualified_signals"] += 1
            if prev_state == watchlist.QUALIFIED and state_long[sym] == watchlist.DIP_ARMED:
                entry_path_long[sym] = "B"
                funnel["long_dip_armed"] += 1
            elif prev_state == watchlist.DIP_ARMED and state_long[sym] == watchlist.ENTRY_EVAL:
                funnel["long_entry_eval_via_dip"] += 1  # entry_path_long[sym] already "B" from the prior bar
```

and in the `if config.shorts_enabled:` block, after the short `next_state_short` call, replace

```python
                if prev_state_s == watchlist.QUALIFIED and state_short[sym] == watchlist.DIP_ARMED:
                    entry_path_short[sym] = "B"
```

with:

```python
                if prev_state_s == watchlist.IDLE and state_short[sym] == watchlist.QUALIFIED:
                    funnel["short_qualified_signals"] += 1
                if prev_state_s == watchlist.QUALIFIED and state_short[sym] == watchlist.DIP_ARMED:
                    entry_path_short[sym] = "B"
                    funnel["short_dip_armed"] += 1
                elif prev_state_s == watchlist.DIP_ARMED and state_short[sym] == watchlist.ENTRY_EVAL:
                    funnel["short_entry_eval_via_dip"] += 1
```

**Trigger-bypass block:** replace the whole block (from `trigger_now = ...` through the end of the short bypass loop) with:

```python
        trigger_now = prepared.bias_df["trigger"].iat[i]
        if trigger_now == LONG_TRIGGER:
            funnel["long_trigger_bars"] += 1
            bias_ok_now = bool(bias_ok_long.iat[i])
            for sym in universe_m5:
                if state_long[sym] != watchlist.QUALIFIED:
                    continue
                funnel["long_trigger_coincidences"] += 1
                if not bias_ok_now:
                    funnel["long_trigger_killed_by_bias_hold"] += 1
                    continue
                gl = bool(prepared.gate_long[sym].iat[i])
                new_state = watchlist.apply_trigger_bypass(state_long[sym], gl, True)
                if new_state != state_long[sym]:
                    state_long[sym] = new_state
                    entry_path_long[sym] = "A"
                    funnel["long_trigger_bypass"] += 1
        if config.shorts_enabled and trigger_now == SHORT_TRIGGER:
            funnel["short_trigger_bars"] += 1
            bias_ok_now_s = bool(bias_ok_short.iat[i])
            for sym in universe_m5:
                if state_short[sym] != watchlist.QUALIFIED:
                    continue
                funnel["short_trigger_coincidences"] += 1
                if not bias_ok_now_s:
                    funnel["short_trigger_killed_by_bias_hold"] += 1
                    continue
                gs = bool(prepared.gate_short[sym].iat[i])
                new_state = watchlist.apply_trigger_bypass(state_short[sym], gs, True)
                if new_state != state_short[sym]:
                    state_short[sym] = new_state
                    entry_path_short[sym] = "A"
                    funnel["short_trigger_bypass"] += 1
```

(Behavior-preserving: the old guard `bias_ok and trigger` is now `trigger` outside / `bias_ok` inside per symbol; a bypass is applied under exactly the same conditions as before.)

**Step 4 of the loop (entry submission), long book:** replace from `# 4. submit entries...` down to (but not including) the `if config.shorts_enabled ...` short block with:

```python
        # 4. submit entries for symbols now in ENTRY_EVAL
        evals_long = [
            sym for sym in universe_m5
            if state_long[sym] == watchlist.ENTRY_EVAL and sym not in positions and sym not in pending
        ]
        if evals_long and not in_entry_window.iat[i]:
            funnel["long_eval_blocked_no_entry_window"] += len(evals_long)
        elif evals_long and not risk_mgr.can_enter(i):
            funnel["long_eval_blocked_risk_halt"] += len(evals_long)
        elif evals_long and not bias_ok_long.iat[i]:
            funnel["long_eval_blocked_bias"] += len(evals_long)
        if can_enter_now and bias_ok_long.iat[i]:
            eligible = {}
            for sym in evals_long:
                if sym in locked_out_long or entries_today_long.get(sym, 0) >= config.max_entries_per_symbol_long:
                    funnel["long_eval_killed_by_lockout_or_cap"] += 1
                    continue
                path = entry_path_long.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_long[sym].iat[i] if path == "A" else prepared.dip_quality_long[sym].iat[i]
                )
                if qualifies:
                    eligible[sym] = prepared.score_long[sym].iat[i]
                else:
                    funnel["long_eval_killed_by_quality"] += 1
            tradeable = watchlist.build_tradeable_list(
                eligible, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            funnel["long_eval_killed_by_ranking"] += len(eligible) - len(tradeable)
            slots_free = (
                config.max_concurrent_long
                - sum(1 for p in positions.values() if p.direction == LONG)
                - sum(1 for o in pending.values() if o["direction"] == LONG)
            )
            funnel["long_eval_killed_by_slots"] += max(0, len(tradeable) - max(0, slots_free))
            for sym in tradeable[:slots_free]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    funnel["long_eval_killed_by_sizing"] += 1
                    continue
                stop = risk.stop_price_long(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
                stop_dist = bar["close"] - stop
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_long[sym].iat[i], LONG,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    funnel["long_eval_killed_by_sizing"] += 1
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, LONG)
                pending[sym] = {"direction": LONG, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}
                funnel["long_orders_submitted"] += 1
```

(The `for sym in evals_long` rewrite is behavior-preserving: `evals_long` applies exactly the
same three exclusions the old first-`continue` did.)

**Short book:** replace the entire `if config.shorts_enabled and can_enter_now and bias_ok_short.iat[i]:` block with:

```python
        evals_short = [
            sym for sym in universe_m5
            if state_short[sym] == watchlist.ENTRY_EVAL and sym not in positions and sym not in pending
        ] if config.shorts_enabled else []
        if evals_short and not in_entry_window.iat[i]:
            funnel["short_eval_blocked_no_entry_window"] += len(evals_short)
        elif evals_short and not risk_mgr.can_enter(i):
            funnel["short_eval_blocked_risk_halt"] += len(evals_short)
        elif evals_short and not bias_ok_short.iat[i]:
            funnel["short_eval_blocked_bias"] += len(evals_short)
        if config.shorts_enabled and can_enter_now and bias_ok_short.iat[i]:
            eligible_s = {}
            for sym in evals_short:
                if sym in locked_out_short or entries_today_short.get(sym, 0) >= config.max_entries_per_symbol_short:
                    funnel["short_eval_killed_by_lockout_or_cap"] += 1
                    continue
                path = entry_path_short.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_short[sym].iat[i] if path == "A" else prepared.bounce_quality_short[sym].iat[i]
                )
                if qualifies:
                    eligible_s[sym] = prepared.score_short[sym].iat[i]
                else:
                    funnel["short_eval_killed_by_quality"] += 1
            tradeable_s = watchlist.build_tradeable_list(
                eligible_s, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            funnel["short_eval_killed_by_ranking"] += len(eligible_s) - len(tradeable_s)
            slots_free_s = (
                config.max_concurrent_short
                - sum(1 for p in positions.values() if p.direction == SHORT)
                - sum(1 for o in pending.values() if o["direction"] == SHORT)
            )
            funnel["short_eval_killed_by_slots"] += max(0, len(tradeable_s) - max(0, slots_free_s))
            for sym in tradeable_s[:slots_free_s]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    funnel["short_eval_killed_by_sizing"] += 1
                    continue
                stop = risk.stop_price_short(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
                stop_dist = stop - bar["close"]
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_short[sym].iat[i], SHORT,
                    short_size_multiplier=config.short_size_multiplier,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    funnel["short_eval_killed_by_sizing"] += 1
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, SHORT)
                pending[sym] = {"direction": SHORT, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}
                funnel["short_orders_submitted"] += 1
```

**Return:** change the final line to
`return BacktestResultM5(trades=trades, equity_curve=equity_series, funnel=funnel)`.

- [ ] **Step 5: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -q` then `python -m pytest -q && ruff check .`
Expected: 3 new tests PASS; every pre-existing test green (identical trades/equity under default config).

- [ ] **Step 6: Wire the CLI**

In `scripts/run_backtest_intraday.py`: add `import json` at the top (stdlib imports before the
`typer` import). After the exit-reason-breakdown block, before `out_dir = ...`, add:

```python
    same_bar_stop_rate = (
        float((trades["entry_time"] == trades["exit_time"]).mean()) if not trades.empty else None
    )
    typer.echo("\nEntry funnel:")
    for k, v in result.funnel.items():
        if v:
            typer.echo(f"  {k}: {v}")
    typer.echo(f"  same_bar_stop_rate: {same_bar_stop_rate}")
```

and after the `trades.to_csv(...)` line, add:

```python
    with open(out_dir / "funnel.json", "w") as f:
        json.dump({**result.funnel, "same_bar_stop_rate": same_bar_stop_rate}, f, indent=2)
```

- [ ] **Step 7: Full suite + lint, then commit**

Run: `python -m pytest -q && ruff check .`
Expected: all green.

```bash
git add src/rs_spy/backtest/engine_m5.py scripts/run_backtest_intraday.py tests/unit/test_engine_m5_backtest.py
git commit -m "M7.5 Phase 0: entry-funnel instrumentation in run_m5_backtest + funnel.json output"
```

---

### Task 4: `run_m5_backtest(..., prepared=)` reuse parameter (matrix 0d, known-limitation #24)

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py` (`run_m5_backtest` signature + docstring)
- Modify: `scripts/run_validation_studies.py:62-64` (pass the baseline `prepared`)
- Modify: `IMPLEMENTATION.md` known-limitations item 24 (mark resolved)
- Test: `tests/unit/test_engine_m5_backtest.py`

**Interfaces:**
- Consumes: `PreparedM5` (unchanged), `_prepare_m5` (unchanged).
- Produces: `run_m5_backtest(..., config: BacktestConfigM5 | None = None, prepared: PreparedM5 | None = None)`. When `prepared` is given, `_prepare_m5` is not called. Contract: the caller guarantees `prepared` was built by `_prepare_m5` with the same universe/data arguments and a config whose **prepare-baked** fields match. Safe to vary against a shared `prepared` (event-loop-only knobs): `risk_per_trade_pct`, `max_concurrent_long/short`, `short_size_multiplier`, `min_list_score`, `min_hold_score`, `top_n_list`, `top_n_tradeable`, `max_per_sector`, `shorts_enabled`, `starting_equity`, `stop_atr_mult`, `max_entries_per_symbol_long/short`, `expected_hold_minutes`, `unfilled_cancel_bars`, and `disabled_gates` **only for `"bias"`**. Baked into `prepared` (varying them requires a fresh `_prepare_m5`): `min_adv_shares`, non-bias `disabled_gates` entries, `rrs_m5_window`, `use_qqq_crosscheck`, all four `rrs_*_threshold_*` fields. This is what makes the Round-3 stop sweep ~free: one precompute, N fast event-loop runs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_engine_m5_backtest.py`:

```python
def test_run_m5_backtest_accepts_a_prebuilt_prepared_and_skips_prepare(monkeypatch):
    """Known-limitation #24: passing prepared= must skip the ~15-20 minute
    _prepare_m5 recompute entirely."""
    sym = "PREP"
    n = 6
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, False)},
    )

    def _explode(*a, **k):
        raise AssertionError("_prepare_m5 must not be called when prepared= is supplied")

    monkeypatch.setattr(engine_m5, "_prepare_m5", _explode)
    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
        prepared=prepared,
    )
    assert result.trades_df().empty  # gate is False throughout; the point is it ran at all


def test_run_m5_backtest_with_prepared_reproduces_the_from_scratch_result(universe):
    """Same config + same data: run_m5_backtest(prepared=...) must be
    bit-for-bit identical to letting it call _prepare_m5 itself."""
    config = BacktestConfigM5()
    kwargs = dict(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=config,
    )
    prepared = _prepare_m5(
        universe_m1=kwargs["universe_m1"], universe_m5=kwargs["universe_m5"],
        universe_d1=kwargs["universe_d1"],
        spy_m1=kwargs["spy_m1"], spy_m5=kwargs["spy_m5"], spy_d1=kwargs["spy_d1"],
        qqq_m1=kwargs["qqq_m1"], qqq_m5=kwargs["qqq_m5"],
        sectors=kwargs["sectors"], config=config,
    )
    r_shared = run_m5_backtest(**kwargs, prepared=prepared)
    r_scratch = run_m5_backtest(**kwargs)
    pd.testing.assert_series_equal(r_shared.equity_curve, r_scratch.equity_curve)
    pd.testing.assert_frame_equal(r_shared.trades_df(), r_scratch.trades_df())
    assert r_shared.funnel == r_scratch.funnel
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -k prepared -v`
Expected: both FAIL — `TypeError: run_m5_backtest() got an unexpected keyword argument 'prepared'`.

- [ ] **Step 3: Implement**

In `engine_m5.py`, change `run_m5_backtest`'s signature and opening lines:

```python
def run_m5_backtest(
    universe_m1: dict,
    universe_m5: dict,
    universe_d1: dict,
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None = None,
    config: BacktestConfigM5 | None = None,
    prepared: PreparedM5 | None = None,
) -> BacktestResultM5:
    """Bar-by-bar M5 event loop. When `prepared` is given, the ~15-20 minute
    _prepare_m5 precompute is skipped and the caller guarantees it was built
    from the SAME universe/data arguments with a config whose prepare-baked
    fields match. Safe to vary against a shared `prepared` (event-loop-only):
    risk_per_trade_pct, max_concurrent_*, short_size_multiplier,
    min_list_score, min_hold_score, top_n_*, max_per_sector, shorts_enabled,
    starting_equity, stop_atr_mult, max_entries_per_symbol_*,
    expected_hold_minutes, unfilled_cancel_bars, and disabled_gates only for
    "bias". Baked into `prepared` (need a fresh _prepare_m5 to vary):
    min_adv_shares, non-bias disabled_gates entries, rrs_m5_window,
    use_qqq_crosscheck, and the four rrs_*_threshold_* fields."""
    config = config or BacktestConfigM5()
    if prepared is None:
        prepared = _prepare_m5(
            universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
            sectors, earnings_blackout, config,
        )
```

(The rest of the body is unchanged.)

In `scripts/run_validation_studies.py`, change the baseline run:

```python
    baseline_result = run_m5_backtest(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
        prepared=baseline_prepared,
    )
```

and update the script's module docstring's run-count remark (line ~8) from
`= ~16 full run_m5_backtest invocations,` to
`= ~16 run_m5_backtest invocations (the baseline shares its own _prepare_m5 via prepared=),`.

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_engine_m5_backtest.py -q` then `python -m pytest -q && ruff check .`
Expected: all green.

- [ ] **Step 5: Update IMPLEMENTATION.md**

Edit known-limitations item 24: wrap the opening clause in `~~...~~` strikethrough and append:

```
**RESOLVED (M7.5 Phase 0).** `run_m5_backtest` now accepts
`prepared: PreparedM5 | None = None` and skips its internal `_prepare_m5` when
given; `scripts/run_validation_studies.py`'s baseline passes its own
`baseline_prepared`. The docstring lists which config fields are event-loop-only
(safe to vary against a shared `prepared` — notably `stop_atr_mult`, making
stop-multiplier sweeps nearly free) versus prepare-baked (need a fresh
`_prepare_m5`).
```

- [ ] **Step 6: Commit**

```bash
git add src/rs_spy/backtest/engine_m5.py scripts/run_validation_studies.py tests/unit/test_engine_m5_backtest.py IMPLEMENTATION.md
git commit -m "M7.5 Phase 0: run_m5_backtest accepts a pre-built PreparedM5 (known-limitation #24)"
```

---

### Task 5: Trigger forward-return study (matrix 0e / D1)

**Files:**
- Create: `src/rs_spy/backtest/studies/trigger_skill_m5.py`
- Create: `scripts/run_trigger_skill_study.py`
- Test: `tests/unit/test_trigger_skill_m5.py`

**Interfaces:**
- Consumes: `rs_spy.bias.engine.bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5) -> pd.DataFrame` with columns `bias`, `trigger`, etc.; `rs_spy.bias.buckets.LONG_TRIGGER / SHORT_TRIGGER / NO_TRIGGER` (string constants).
- Produces: `trigger_skill_table(trigger: pd.Series, close: pd.Series, horizons=(6, 12, 24), flat_threshold_pct=0.001) -> pd.DataFrame` (pure, testable) and `run_trigger_skill_m5(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5, horizons=..., flat_threshold_pct=...) -> pd.DataFrame` (wrapper). Output columns: `horizon_bars`, `signal` (`ALL`/`LONG_TRIGGER`/`SHORT_TRIGGER`), `n`, `pct_up`, `pct_flat`, `pct_down`, `mean_fwd_return`, `median_fwd_return`. The script writes `reports/tuning/trigger_skill.csv`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_trigger_skill_m5.py`:

```python
import pandas as pd
import pytest

from rs_spy.backtest.studies.trigger_skill_m5 import trigger_skill_table
from rs_spy.bias.buckets import LONG_TRIGGER, NO_TRIGGER, SHORT_TRIGGER


def _calendar(n):
    return pd.date_range("2026-03-02 14:30", periods=n, freq="5min", tz="UTC")


def test_trigger_skill_table_hand_computed_long_rows():
    idx = _calendar(10)
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109], index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    trigger.iloc[1] = LONG_TRIGGER
    trigger.iloc[4] = LONG_TRIGGER
    trigger.iloc[9] = LONG_TRIGGER  # no bar 2 ahead -> forward return NaN -> excluded from n

    out = trigger_skill_table(trigger, close, horizons=(2,), flat_threshold_pct=0.001)

    long_row = out[(out["signal"] == LONG_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert long_row["n"] == 2
    assert long_row["pct_up"] == 1.0
    assert long_row["pct_down"] == 0.0
    expected_mean = ((103 / 101 - 1) + (106 / 104 - 1)) / 2
    assert long_row["mean_fwd_return"] == pytest.approx(expected_mean)

    all_row = out[(out["signal"] == "ALL") & (out["horizon_bars"] == 2)].iloc[0]
    assert all_row["n"] == 8  # bars 0..7 have a bar 2 ahead
    assert all_row["pct_up"] == 1.0  # monotonically rising fixture

    short_row = out[(out["signal"] == SHORT_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert short_row["n"] == 0
    assert short_row["pct_up"] is None


def test_trigger_skill_table_classifies_down_and_flat_moves():
    idx = _calendar(8)
    # bars 0-3 fall hard, bars 4-7 are pinned flat
    close = pd.Series([100.0, 98.0, 96.0, 94.0, 92.0, 92.0, 92.0, 92.0], index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    trigger.iloc[0] = SHORT_TRIGGER  # fwd(2) = 96/100 - 1 = -4% -> DOWN
    trigger.iloc[4] = SHORT_TRIGGER  # fwd(2) = 92/92 - 1 = 0% -> FLAT

    out = trigger_skill_table(trigger, close, horizons=(2,), flat_threshold_pct=0.001)
    short_row = out[(out["signal"] == SHORT_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert short_row["n"] == 2
    assert short_row["pct_down"] == 0.5
    assert short_row["pct_flat"] == 0.5
    assert short_row["pct_up"] == 0.0


def test_trigger_skill_table_emits_one_row_per_signal_per_horizon():
    idx = _calendar(30)
    close = pd.Series(100.0, index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    out = trigger_skill_table(trigger, close, horizons=(2, 6), flat_threshold_pct=0.001)
    assert len(out) == 6  # 2 horizons x (ALL, LONG_TRIGGER, SHORT_TRIGGER)
    assert set(out["signal"]) == {"ALL", LONG_TRIGGER, SHORT_TRIGGER}
    assert set(out["horizon_bars"]) == {2, 6}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_trigger_skill_m5.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.backtest.studies.trigger_skill_m5'`.

- [ ] **Step 3: Implement the study module**

Create `src/rs_spy/backtest/studies/trigger_skill_m5.py`:

```python
"""M7.5 Phase 0 (tuning-matrix cell D1): trigger forward-return study.

The M7 bias confusion matrix (bias_confusion_m5.py) tested the bias BUCKET
and found ~zero directional skill above base rate -- but the signal that
actually gates 100% of the real backtest's entries is the trendline-breach
TRIGGER (bias_df["trigger"]), which had never been tested in isolation. This
study is the analog of real-life practice timing SPY entries off a
market-timing oscillator signal (OneOption's "1OP cross"): for every
LONG_TRIGGER / SHORT_TRIGGER fire, classify SPY's own forward return over
each horizon as UP/FLAT/DOWN and compare against the all-bars base rate.
Fires have real sample sizes (~1,591 long / ~561 short over 5 years), unlike
the 3-trade backtest sample. Needs no backtest run -- only the bias engine's
own output and SPY's M5 close series.
"""
import pandas as pd

from rs_spy.bias.buckets import LONG_TRIGGER, SHORT_TRIGGER
from rs_spy.bias.engine import bias_series

DEFAULT_HORIZONS = (6, 12, 24)  # 30 min / 1 h / 2 h at M5 cadence
DEFAULT_FLAT_THRESHOLD_PCT = 0.001  # same flat band as bias_confusion_m5.py


def trigger_skill_table(
    trigger: pd.Series,
    close: pd.Series,
    horizons: tuple = DEFAULT_HORIZONS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> pd.DataFrame:
    """One row per (horizon, signal) for signal in ALL / LONG_TRIGGER /
    SHORT_TRIGGER. Bars whose forward return is undefined (fewer than
    `horizon` bars of subsequent history) are excluded from `n`. The ALL row
    is the base rate every trigger row must beat to claim any skill."""
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for label, mask in (
            ("ALL", pd.Series(True, index=trigger.index)),
            (LONG_TRIGGER, trigger == LONG_TRIGGER),
            (SHORT_TRIGGER, trigger == SHORT_TRIGGER),
        ):
            sub = fwd[mask & fwd.notna()]
            n = len(sub)
            rows.append(
                {
                    "horizon_bars": horizon,
                    "signal": label,
                    "n": n,
                    "pct_up": float((sub > flat_threshold_pct).mean()) if n else None,
                    "pct_flat": float((sub.abs() <= flat_threshold_pct).mean()) if n else None,
                    "pct_down": float((sub < -flat_threshold_pct).mean()) if n else None,
                    "mean_fwd_return": float(sub.mean()) if n else None,
                    "median_fwd_return": float(sub.median()) if n else None,
                }
            )
    return pd.DataFrame(rows)


def run_trigger_skill_m5(
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
    horizons: tuple = DEFAULT_HORIZONS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> pd.DataFrame:
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    return trigger_skill_table(bias_df["trigger"], spy_m5["close"], horizons, flat_threshold_pct)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_trigger_skill_m5.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Create the CLI script**

Create `scripts/run_trigger_skill_study.py`:

```python
"""M7.5 Phase 0 (tuning-matrix cell D1): run the trigger forward-return study
against the real cached warehouse. Only needs SPY/QQQ bars (no universe, no
backtest) -- runs in a minute or two, not 15-20. Writes
reports/tuning/trigger_skill.csv."""
import typer

from rs_spy.backtest.studies.trigger_skill_m5 import run_trigger_skill_m5
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_universe

app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    con = connect(settings.resolved_warehouse_path())

    benchmarks = [spy, qqq]
    all_m1 = load_universe_m1_bars(con, benchmarks)
    all_m5 = load_universe_m5_bars(con, benchmarks)
    all_d1 = load_universe_daily_bars(con, benchmarks)

    typer.echo("Computing bias series + trigger forward returns (SPY/QQQ only)...")
    table = run_trigger_skill_m5(
        all_m1[spy], all_m5[spy], all_d1[spy], all_m1[qqq], all_m5[qqq],
    )
    typer.echo(table.to_string(index=False))

    out_dir = settings.reports_dir / "tuning"
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "trigger_skill.csv", index=False)
    typer.echo(f"\nWrote {out_dir / 'trigger_skill.csv'}")


if __name__ == "__main__":
    app()
```

Note: `scripts/run_backtest_intraday.py` uses this exact loader/settings pattern — mirror it, don't invent new wiring. Do not run this script in the task (it needs the real warehouse); running it is the milestone's post-build step.

- [ ] **Step 6: Full suite + lint, then commit**

Run: `python -m pytest -q && ruff check .`
Expected: all green.

```bash
git add src/rs_spy/backtest/studies/trigger_skill_m5.py scripts/run_trigger_skill_study.py tests/unit/test_trigger_skill_m5.py
git commit -m "M7.5 Phase 0: trigger forward-return study (matrix D1) + CLI script"
```

---

## Post-plan step (main session, after all 5 tasks reviewed and merged)

Not a subagent task — the driver runs these against the real warehouse and records results:

1. `python scripts/run_trigger_skill_study.py` (~1-2 min) → read `reports/tuning/trigger_skill.csv`; the D1 result shapes Round 4's weight.
2. `python scripts/run_backtest_intraday.py` (~15-20 min) → confirms the default-config result is still exactly 3 trades (behavior preservation on real data) and produces the first real `funnel.json` — copy its counters into the `m7-baseline` row's blank funnel columns in `docs/tuning/ledger.csv`.
3. Update `IMPLEMENTATION.md` with a short "M7.5 Phase 0" section and commit the ledger update.
