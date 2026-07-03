# M6: M5 Event-Driven Backtest Engine + Long/Short Algo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the long/short trading algorithms (algo-spec 05/06), the risk/sizing
layer (algo-spec 07), and an M5-cadence event-driven backtest engine that wires them
to the M5 bias engine (`bias/engine.py`) and stock-selection engine
(`selection/{gates,scoring,watchlist}.py`) already completed in M5 — producing a real
intraday trade log for the curated universe.

**Architecture:** Mirrors the D1 walking skeleton's own architecture
(`backtest/engine.py`) one level up in cadence and richness: a precompute phase runs
every M5-cadence indicator/gate/score function once per symbol over its own full
history, then a single chronological M5-bar event loop drives watchlist state,
entries (via `algo/long.py`/`algo/short.py`), position management, and order fills
(via `backtest/broker_sim.py`), reading risk/sizing decisions from `algo/risk.py`.
Position-management rules are implemented as small, independently-testable
vectorized pandas Series (same style as `selection/gates.py`), not as one large
stateful function — the event loop's job is orchestration, not rule logic.

**Tech Stack:** Python 3.14, pandas/numpy (existing project stack — no new
dependencies), pytest (existing test style — see `tests/unit/test_engine_m5.py`,
`tests/unit/test_gates_m5.py` for the fixture conventions this plan's tests follow).

## Global Constraints

- Every new indicator/signal function is a pure vectorized pandas function
  (`f(df_or_series, **params) -> pd.Series`), backward-looking only — no
  `.shift(-n)`, no future data. This is the project's single most important
  invariant (see `tests/unit/test_no_lookahead.py` precedent) and every task below
  must preserve it.
- **Reindexing order (read this before Task 5 — the #1 correctness risk in this
  milestone):** compute every per-symbol M5-cadence quantity (features, ATR, EMA8,
  gates, scores, exit-signal Series) on that symbol's OWN native M5 index FIRST.
  Only reindex the *finished* per-bar outputs onto the shared master calendar
  (`spy_m5.index`) as the LAST step, using a strict `.reindex(calendar)` — **never**
  `.reindex(calendar).ffill()`. A master-calendar bar a thinly-traded symbol has no
  native bar for must read as "no fresh signal" (NaN for floats, `False` for
  booleans via `fill_value=False`), not a stale carried-forward value. Reindexing
  raw OHLCV or an intermediate series *before* computing a rolling/EWM quantity on
  it would inject NaN gaps into that rolling window and corrupt every value near the
  gap, not just the gap bar itself — this is exactly the kind of bug the M5
  milestone's lookahead fix (`_close_label` in `bias/engine.py`/`features_m5.py`)
  was about, one layer up.
- Reuse, do not reimplement, anything already built: `bias.engine.bias_series`,
  `selection.features_m5.compute_symbol_features_m5`,
  `selection.gates.gates_pass_long_m5`/`gates_pass_short_m5`,
  `selection.scoring.score_long_m5`/`score_short_m5`, `selection.watchlist`
  (`next_state_long`/`next_state_short`, `apply_trigger_bypass`,
  `build_tradeable_list`), `indicators.atr.atr`, `indicators.candle_structure`
  (`stacked_count`, `chop_ratio`), `data.resample`
  (`align_causal`/`align_daily_to_intraday`/`resample_ohlcv`),
  `bias.daily_context.daily_context_series`, `bias.buckets`
  (`BULL`/`STRONG_BULL`/`BEAR`/`STRONG_BEAR`/`NEUTRAL`),
  `bias.trigger` (`LONG_TRIGGER`/`SHORT_TRIGGER`).
- Exact spec values (copy verbatim, do not re-derive): entry window **10:15–15:30
  ET** (05 §1.1, 06 §1.1); time-flat **15:55 ET** (05 §4.7, 06 §4); final-30-minute
  window **15:30–16:00 ET** with profit-take thresholds reduced **25%** (07 §4);
  risk per trade **0.5%** of equity (07 §1); max concurrent **5 long / 3 short**
  (07 §1); daily loss limit **-2.0%**, weekly loss limit **-4.0%** (07 §1);
  consecutive stop-outs **3** in a day → halt new entries **2 hours** (07 §1); short
  size multiplier **0.75×** (06 §5, 07 §2); entry limit offset **0.1×ATR_M5** (07
  §5); unfilled-entry cancel after **2 M5 bars** (07 §5); slippage **2 bps** default
  extra cost (08 §1 — the spec's own "½ spread + 2bps" starting point; this project
  already carries a `slippage_extra_bps: 2.0` default in `config/backtest_default.yaml`
  with `slippage_half_spread_bps: 0.0` as a placeholder pending real calibration, so
  this plan uses 2.0 bps flat, matching that existing config, not re-deriving a new
  number); stop = **1.0×ATR_M5(14)**, never wider than **1.5×ATR_M5** (07 §3 — see
  Task 1's documented stop simplification); trail trigger **1.5×ATR_M5**, trail
  distance **0.25×ATR_M5** off EMA8(M5) (05 §4.6/06 §4); profit-take trigger
  **1.0×ATR_M5** gain with LRSI crossing **80** (long) / **20** (short) (05
  §4.5/06 §4), chop regime target **0.75×** the normal target; RS-failure **2
  consecutive M5 bars** (05 §4.3/06 §4); VWAP-loss **2 consecutive M5 closes** (05
  §4.4/06 §4); market-flip stacked/RVOL confirmation **stacked ≥3, RVOL ≥1.5**
  (already `bias/engine.py`'s `FLIP_STACK_THRESHOLD`/`FLIP_RVOL_THRESHOLD` — reused,
  not redefined) for the long side, **unconditional** for the short-to-bull flip (06
  §4, explicitly asymmetric); short squeeze guard **≥2.0×ATR_M5 on RVOL ≥2.0** (06
  §4); max entries per symbol per day **2 long / 1 short**, session lockout after a
  hard-stop loss (05 §5/06 §5); dynamic NEUTRAL-bias stop tightening to **entry -
  0.5×ATR_M5, or breakeven if better** (07 §4).
- Deliberately NOT built this milestone (document in Task 8, do not silently drop):
  07 §6's kill switches (data-feed-gap detection, broker-reject retry, clock-skew
  halt, halted-symbol handling) are live-trading operational concerns with no
  analog against clean historical bars already in the warehouse. 07 §3's
  "qualifying dip's swing low" stop alternative is replaced by the ATR-only
  distance (see Task 1). 05 §3/06 §3's discretionary dip/bounce-quality language is
  translated into this project's existing indicator vocabulary (see Task 3/4) —
  disclosed as a translation, the same pattern already used for
  `bias/daily_context.py`'s `suspect_rally` breakout audit.
- Short book stays **off by default** (`shorts_enabled=False` in the new
  `BacktestConfigM5`), matching 06's own recommended default and
  `backtest/engine.py`'s existing D1 convention — but the mechanism must be fully
  built, tested, and runnable via a config flag, not stubbed.
- Position-management rule order for every open position, evaluated once per M5 bar
  in this exact order (05 §4/06 §4) — **a position closes on the first rule that
  fires; later rules are not evaluated once one has**:
  1. Hard stop (resting order — checked against this bar's low/high, before any
     signal-based rule)
  2. *(short only)* Squeeze guard
  3. Market flip
  4. RS failure
  5. VWAP loss
  6. Momentum-stall profit-take
  7. Trail (updates the stop, does not itself close the position)
  8. Time-flat (15:55 ET, market order)

---

## Task 1: `algo/risk.py` — position sizing + account-level risk guards

**Files:**
- Create: `src/rs_spy/algo/risk.py`
- Test: `tests/unit/test_risk.py`

**Interfaces:**
- Consumes: `rs_spy.bias.buckets.{BULL,STRONG_BULL,BEAR,STRONG_BEAR}` (string
  constants, already defined).
- Produces (used by Task 6):
  - `stop_price_long(entry: float, atr_m5: float) -> float`
  - `stop_price_short(entry: float, atr_m5: float) -> float`
  - `neutral_tighten_stop_long(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float`
  - `neutral_tighten_stop_short(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float`
  - `bias_size_multiplier(bias: str) -> float`
  - `score_size_multiplier(score: float) -> float`
  - `position_size(equity: float, risk_per_trade_pct: float, stop_distance: float, bias: str, score: float, direction: str, short_size_multiplier: float = 0.75) -> float`
    (uncapped share count; Task 6 applies `cap_shares` before using it)
  - `cap_shares(shares: float, entry_price: float, equity: float, adv20: float, expected_hold_minutes: float, max_notional_pct: float = 0.20, max_participation_pct: float = 0.05, bars_per_session: int = 390) -> float`
  - `class RiskManager` with `__init__(self, starting_equity: float)`,
    `.can_enter(self, bar_index: int) -> bool`,
    `.register_exit(self, pnl: float, equity: float, exit_reason: str, bar_index: int) -> None`,
    `.new_session(self, equity: float) -> None`,
    `.new_week(self, equity: float) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_risk.py
import pytest

from rs_spy.algo.risk import (
    RiskManager,
    bias_size_multiplier,
    cap_shares,
    neutral_tighten_stop_long,
    neutral_tighten_stop_short,
    position_size,
    score_size_multiplier,
    stop_price_long,
    stop_price_short,
)
from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL


def test_stop_price_long_and_short():
    assert stop_price_long(entry=100.0, atr_m5=2.0) == pytest.approx(98.0)
    assert stop_price_short(entry=100.0, atr_m5=2.0) == pytest.approx(102.0)


def test_bias_size_multiplier():
    assert bias_size_multiplier(STRONG_BULL) == pytest.approx(1.0)
    assert bias_size_multiplier(STRONG_BEAR) == pytest.approx(1.0)
    assert bias_size_multiplier(BULL) == pytest.approx(0.75)
    assert bias_size_multiplier(BEAR) == pytest.approx(0.75)


def test_score_size_multiplier_maps_50_to_100_onto_0p7_to_1p0():
    assert score_size_multiplier(50.0) == pytest.approx(0.7)
    assert score_size_multiplier(100.0) == pytest.approx(1.0)
    assert score_size_multiplier(75.0) == pytest.approx(0.85)
    # clips outside [50, 100]
    assert score_size_multiplier(20.0) == pytest.approx(0.7)
    assert score_size_multiplier(150.0) == pytest.approx(1.0)


def test_position_size_matches_hand_computed_example():
    # equity=100k, risk 0.5% => $500 risk. stop_distance=2.0 => base_shares=250.
    # BULL (not STRONG) => bias_mult=0.75. score=75 => score_mult=0.85. LONG => side_mult=1.0.
    # 250 * 0.75 * 0.85 * 1.0 = 159.375
    shares = position_size(
        equity=100_000.0,
        risk_per_trade_pct=0.005,
        stop_distance=2.0,
        bias=BULL,
        score=75.0,
        direction="LONG",
    )
    assert shares == pytest.approx(159.375)


def test_position_size_short_applies_side_multiplier():
    long_shares = position_size(100_000.0, 0.005, 2.0, STRONG_BULL, 100.0, "LONG")
    short_shares = position_size(100_000.0, 0.005, 2.0, STRONG_BEAR, 100.0, "SHORT")
    assert short_shares == pytest.approx(long_shares * 0.75)


def test_position_size_zero_stop_distance_returns_zero():
    assert position_size(100_000.0, 0.005, 0.0, BULL, 75.0, "LONG") == 0.0


def test_cap_shares_notional_cap_binds():
    # equity=100k, max_notional_pct=0.20 => $20k notional cap. entry=50 => 400 shares cap.
    capped = cap_shares(
        shares=1000.0,
        entry_price=50.0,
        equity=100_000.0,
        adv20=10_000_000.0,
        expected_hold_minutes=120.0,
    )
    assert capped == pytest.approx(400.0)


def test_cap_shares_participation_cap_binds():
    # adv20=1,000,000; 5% of that = 50,000; /390*120 = 15,384.6...
    capped = cap_shares(
        shares=1_000_000.0,
        entry_price=1.0,
        equity=100_000_000.0,
        adv20=1_000_000.0,
        expected_hold_minutes=120.0,
    )
    assert capped == pytest.approx(15384.0, abs=1.0)


def test_cap_shares_floors_to_whole_share():
    capped = cap_shares(159.9, entry_price=10.0, equity=1_000_000.0, adv20=10_000_000.0, expected_hold_minutes=120.0)
    assert capped == 159.0


def test_neutral_tighten_stop_long_moves_toward_price_only():
    # entry=100, atr=4 => candidate = 100 - 0.5*4 = 98. current_stop=95 (looser) => tightens to 98.
    tightened = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=95.0, current_price=101.0)
    assert tightened == pytest.approx(98.0)
    # current_stop already tighter than candidate => stays put (never loosens)
    tightened2 = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=99.0, current_price=101.0)
    assert tightened2 == pytest.approx(99.0)


def test_neutral_tighten_stop_long_uses_breakeven_when_favorable_enough():
    # price has moved up by >= 0.5*atr (2.0) beyond entry => breakeven (100) is used
    # instead of the plain candidate (98), since it's the more protective of the two.
    tightened = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=90.0, current_price=103.0)
    assert tightened == pytest.approx(100.0)


def test_neutral_tighten_stop_short_mirrors_long():
    tightened = neutral_tighten_stop_short(entry=100.0, atr_m5=4.0, current_stop=105.0, current_price=99.0)
    assert tightened == pytest.approx(102.0)
    tightened2 = neutral_tighten_stop_short(entry=100.0, atr_m5=4.0, current_stop=101.0, current_price=99.0)
    assert tightened2 == pytest.approx(101.0)


def test_risk_manager_consecutive_stopouts_halt_new_entries_for_24_bars():
    rm = RiskManager(starting_equity=100_000.0)
    assert rm.can_enter(bar_index=0)
    rm.register_exit(pnl=-100.0, equity=99_900.0, exit_reason="hard_stop", bar_index=10)
    rm.register_exit(pnl=-100.0, equity=99_800.0, exit_reason="hard_stop", bar_index=20)
    assert rm.can_enter(bar_index=25)  # only 2 so far
    rm.register_exit(pnl=-100.0, equity=99_700.0, exit_reason="hard_stop", bar_index=30)
    assert not rm.can_enter(bar_index=31)
    assert not rm.can_enter(bar_index=53)  # 30 + 24 - 1
    assert rm.can_enter(bar_index=54)  # 30 + 24


def test_risk_manager_non_stopout_exit_resets_consecutive_count():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(-100.0, 99_900.0, "hard_stop", 1)
    rm.register_exit(-100.0, 99_800.0, "hard_stop", 2)
    rm.register_exit(50.0, 99_850.0, "profit_take", 3)
    rm.register_exit(-100.0, 99_750.0, "hard_stop", 4)
    assert rm.can_enter(bar_index=5)  # count reset to 1 after the profit_take


def test_risk_manager_daily_loss_limit_halts_until_new_session():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(pnl=-2_100.0, equity=97_900.0, exit_reason="rs_failure", bar_index=1)
    assert not rm.can_enter(bar_index=2)
    rm.new_session(equity=97_900.0)
    assert rm.can_enter(bar_index=3)


def test_risk_manager_weekly_loss_limit_halts_until_new_week():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(pnl=-4_100.0, equity=95_900.0, exit_reason="rs_failure", bar_index=1)
    assert not rm.can_enter(bar_index=2)
    rm.new_session(equity=95_900.0)
    assert not rm.can_enter(bar_index=3)  # weekly halt survives a new session
    rm.new_week(equity=95_900.0)
    assert rm.can_enter(bar_index=4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_risk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.algo.risk'`

- [ ] **Step 3: Write the implementation**

```python
# src/rs_spy/algo/risk.py
"""Position sizing + account-level risk guards. algo-spec/07-risk-management.md.

Implements what a historical-bar backtest can meaningfully simulate: §2 position
sizing, §3 stops (ATR-only distance -- see below), the §1 daily/weekly loss limits
and consecutive-stop-out halt, and §4's two dynamic-tightening rules (this module
has the NEUTRAL-bias stop-tightening formula; the final-30-minute profit-take
reduction lives in algo/long.py|short.py since it's evaluated alongside the other
exit rules, not here).

Deliberately NOT implemented: §6's kill switches (data-feed-gap detection,
broker-reject retry, clock-skew halt, halted-symbol handling) are live-trading
operational concerns with no analog against clean historical bars already sitting
in the warehouse -- there is no "feed" to gap in a backtest.

Stop placement (§3) is simplified: the spec's "lowest of the qualifying dip's swing
low and (entry - 1.0*ATR_M5)" alternative is reduced to the ATR-only distance --
this project doesn't track "the qualifying dip's swing low" as distinct structure
(matching backtest/engine.py's own D1 stop precedent, which is also ATR-only
distance rather than structure-based). Consequence: the resulting stop distance
(1.0xATR) is always inside the spec's 1.5xATR cap, so the "skip the entry rather
than widen the stop" branch never triggers under this simplification -- disclosed,
not silently dropped.

"or breakeven if better" (§4's NEUTRAL-bias tightening rule) is a discretionary
four words with no formula given. Read here as: tighten toward entry - 0.5*ATR,
but if the position has already moved favorably by at least that same 0.5*ATR
beyond entry, tighten to breakeven instead (the more protective of the two once
that much cushion exists) -- and never move the stop away from price, matching
§2's "stops are never moved away from price."
"""
from dataclasses import dataclass, field

from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL

STOP_ATR_MULT = 1.0
STOP_ATR_CAP_MULT = 1.5  # documented no-op under the ATR-only simplification above
NEUTRAL_TIGHTEN_ATR_MULT = 0.5
MAX_NOTIONAL_PCT = 0.20
MAX_PARTICIPATION_PCT = 0.05
BARS_PER_SESSION = 390

STRONG_BIAS_MULT = 1.0
NORMAL_BIAS_MULT = 0.75
SCORE_MULT_LOW = 0.7
SCORE_MULT_HIGH = 1.0
SCORE_LOW = 50.0
SCORE_HIGH = 100.0
SHORT_SIDE_MULT = 0.75

CONSECUTIVE_STOPOUT_LIMIT = 3
CONSECUTIVE_STOPOUT_HALT_BARS = 24  # 2 hours / 5-minute bars
DAILY_LOSS_LIMIT_PCT = -0.02
WEEKLY_LOSS_LIMIT_PCT = -0.04


def stop_price_long(entry: float, atr_m5: float) -> float:
    return entry - min(STOP_ATR_MULT, STOP_ATR_CAP_MULT) * atr_m5


def stop_price_short(entry: float, atr_m5: float) -> float:
    return entry + min(STOP_ATR_MULT, STOP_ATR_CAP_MULT) * atr_m5


def neutral_tighten_stop_long(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float:
    candidate = entry - NEUTRAL_TIGHTEN_ATR_MULT * atr_m5
    if current_price - entry >= NEUTRAL_TIGHTEN_ATR_MULT * atr_m5:
        candidate = max(candidate, entry)
    return max(current_stop, candidate)


def neutral_tighten_stop_short(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float:
    candidate = entry + NEUTRAL_TIGHTEN_ATR_MULT * atr_m5
    if entry - current_price >= NEUTRAL_TIGHTEN_ATR_MULT * atr_m5:
        candidate = min(candidate, entry)
    return min(current_stop, candidate)


def bias_size_multiplier(bias: str) -> float:
    return STRONG_BIAS_MULT if bias in (STRONG_BULL, STRONG_BEAR) else NORMAL_BIAS_MULT


def score_size_multiplier(score: float) -> float:
    frac = (score - SCORE_LOW) / (SCORE_HIGH - SCORE_LOW)
    frac = min(max(frac, 0.0), 1.0)
    return SCORE_MULT_LOW + frac * (SCORE_MULT_HIGH - SCORE_MULT_LOW)


def position_size(
    equity: float,
    risk_per_trade_pct: float,
    stop_distance: float,
    bias: str,
    score: float,
    direction: str,
    short_size_multiplier: float = SHORT_SIDE_MULT,
) -> float:
    """Uncapped share count (07 §2). Caller must apply cap_shares() before use."""
    if stop_distance <= 0:
        return 0.0
    base_shares = (equity * risk_per_trade_pct) / stop_distance
    side_mult = short_size_multiplier if direction == "SHORT" else 1.0
    return base_shares * bias_size_multiplier(bias) * score_size_multiplier(score) * side_mult


def cap_shares(
    shares: float,
    entry_price: float,
    equity: float,
    adv20: float,
    expected_hold_minutes: float,
    max_notional_pct: float = MAX_NOTIONAL_PCT,
    max_participation_pct: float = MAX_PARTICIPATION_PCT,
    bars_per_session: int = BARS_PER_SESSION,
) -> float:
    """07 §2: notional <= max_notional_pct of equity; shares <=
    max_participation_pct of 20-day ADV / bars_per_session * expected_hold_minutes
    (a participation-rate sanity cap, not a literal fill-rate model)."""
    if entry_price <= 0:
        return 0.0
    notional_cap = (equity * max_notional_pct) / entry_price
    participation_cap = (max_participation_pct * adv20 / bars_per_session) * expected_hold_minutes
    capped = min(shares, notional_cap, participation_cap)
    return float(max(capped, 0.0) // 1)


@dataclass
class RiskManager:
    """Account-level guards (07 §1): daily/weekly loss limits and the
    consecutive-stop-out halt. One instance drives the whole backtest; the caller
    (backtest/engine_m5.py) calls new_session()/new_week() on calendar-day/week
    boundaries and register_exit() after every closed trade."""

    starting_equity: float
    starting_equity_today: float = field(init=False)
    starting_equity_week: float = field(init=False)
    consecutive_stopouts_today: int = field(default=0, init=False)
    halt_until_bar: int | None = field(default=None, init=False)
    daily_halted: bool = field(default=False, init=False)
    weekly_halted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.starting_equity_today = self.starting_equity
        self.starting_equity_week = self.starting_equity

    def can_enter(self, bar_index: int) -> bool:
        if self.daily_halted or self.weekly_halted:
            return False
        if self.halt_until_bar is not None and bar_index < self.halt_until_bar:
            return False
        return True

    def register_exit(self, pnl: float, equity: float, exit_reason: str, bar_index: int) -> None:
        if exit_reason == "hard_stop":
            self.consecutive_stopouts_today += 1
            if self.consecutive_stopouts_today >= CONSECUTIVE_STOPOUT_LIMIT:
                self.halt_until_bar = bar_index + CONSECUTIVE_STOPOUT_HALT_BARS
        else:
            self.consecutive_stopouts_today = 0

        if (equity - self.starting_equity_today) / self.starting_equity_today <= DAILY_LOSS_LIMIT_PCT:
            self.daily_halted = True
        if (equity - self.starting_equity_week) / self.starting_equity_week <= WEEKLY_LOSS_LIMIT_PCT:
            self.weekly_halted = True

    def new_session(self, equity: float) -> None:
        self.starting_equity_today = equity
        self.consecutive_stopouts_today = 0
        self.halt_until_bar = None
        self.daily_halted = False

    def new_week(self, equity: float) -> None:
        self.starting_equity_week = equity
        self.weekly_halted = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_risk.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/algo/risk.py tests/unit/test_risk.py
git commit -m "M6 Task 1: position sizing + account-level risk guards (algo/risk.py)"
```

---

## Task 2: `backtest/broker_sim.py` — order fill simulation

**Files:**
- Create: `src/rs_spy/backtest/broker_sim.py`
- Test: `tests/unit/test_broker_sim.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure, standalone module).
- Produces (used by Task 6):
  - `ENTRY_LIMIT_ATR_MULT = 0.1`, `DEFAULT_UNFILLED_CANCEL_BARS = 2`, `SLIPPAGE_BPS = 2.0` (module constants)
  - `entry_limit_price(last_price: float, atr_m5: float, direction: str) -> float`
  - `try_fill_entry(direction: str, limit_price: float, bar_open: float, bar_high: float, bar_low: float) -> float | None`
  - `apply_slippage(price: float, direction: str, is_entry: bool, bps: float = SLIPPAGE_BPS) -> float`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_broker_sim.py
import pytest

from rs_spy.backtest.broker_sim import apply_slippage, entry_limit_price, try_fill_entry


def test_entry_limit_price_long_and_short():
    assert entry_limit_price(last_price=100.0, atr_m5=2.0, direction="LONG") == pytest.approx(100.2)
    assert entry_limit_price(last_price=100.0, atr_m5=2.0, direction="SHORT") == pytest.approx(99.8)


def test_try_fill_entry_long_fills_at_limit_when_open_gaps_through_unfavorably():
    # bar opens above the limit (worse for a buyer) but trades back down through it
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=100.5, bar_high=100.6, bar_low=100.1)
    assert fill == pytest.approx(100.2)


def test_try_fill_entry_long_fills_at_open_when_open_gaps_through_favorably():
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=99.9, bar_high=100.0, bar_low=99.8)
    assert fill == pytest.approx(99.9)


def test_try_fill_entry_long_no_fill_when_bar_never_reaches_limit():
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=100.5, bar_high=100.8, bar_low=100.3)
    assert fill is None


def test_try_fill_entry_short_mirrors_long():
    fill = try_fill_entry("SHORT", limit_price=99.8, bar_open=99.5, bar_high=99.9, bar_low=99.4)
    assert fill == pytest.approx(99.8)
    fill2 = try_fill_entry("SHORT", limit_price=99.8, bar_open=100.1, bar_high=100.2, bar_low=100.0)
    assert fill2 is None


def test_apply_slippage_long_entry_and_exit():
    entry = apply_slippage(100.0, direction="LONG", is_entry=True, bps=2.0)
    exit_ = apply_slippage(100.0, direction="LONG", is_entry=False, bps=2.0)
    assert entry == pytest.approx(100.02)
    assert exit_ == pytest.approx(99.98)


def test_apply_slippage_short_entry_and_exit():
    entry = apply_slippage(100.0, direction="SHORT", is_entry=True, bps=2.0)
    exit_ = apply_slippage(100.0, direction="SHORT", is_entry=False, bps=2.0)
    assert entry == pytest.approx(99.98)
    assert exit_ == pytest.approx(100.02)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_broker_sim.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.backtest.broker_sim'`

- [ ] **Step 3: Write the implementation**

```python
# src/rs_spy/backtest/broker_sim.py
"""Order fill simulation. algo-spec/07 §5, 08 §1.

Entries: marketable-limit orders (07 §5) -- long limit = last + 0.1*ATR_M5, short
limit = last - 0.1*ATR_M5 -- filled on the bar AFTER the one that produced the
entry signal (08 §1's "fills at next-bar prices" no-lookahead rule; the caller in
backtest/engine_m5.py is responsible for only calling try_fill_entry on bars after
the signal bar), at the better of the limit price or that bar's own open, provided
the bar's range actually reaches the limit; unfilled after
DEFAULT_UNFILLED_CANCEL_BARS bars, the caller cancels ("never chase -- the state
machine will re-arm").

Exits: market orders, filled at the same bar's close that produced the exit signal
-- matching backtest/engine.py's existing D1 convention (an exit decided from bar
i's own closed data fills at bar i's own close, not a stricter next-bar model);
07 §5's "getting out matters more than the fill" supports treating same-bar-close
as an acceptable approximation. This module only provides the slippage adjustment
for that fill price -- the fill price itself (bar close, or the stop level on a
hard-stop exit) is computed by the caller, same as backtest/engine.py's D1 pattern.
"""

ENTRY_LIMIT_ATR_MULT = 0.1
DEFAULT_UNFILLED_CANCEL_BARS = 2
SLIPPAGE_BPS = 2.0


def entry_limit_price(last_price: float, atr_m5: float, direction: str) -> float:
    offset = ENTRY_LIMIT_ATR_MULT * atr_m5
    return last_price + offset if direction == "LONG" else last_price - offset


def try_fill_entry(direction: str, limit_price: float, bar_open: float, bar_high: float, bar_low: float) -> float | None:
    """Returns the fill price if this bar's range reaches the limit, else None
    (order stays pending). Fill price is the better of the limit and the bar's
    open -- never worse than the limit itself."""
    if direction == "LONG":
        if bar_low <= limit_price:
            return min(limit_price, bar_open) if bar_open <= limit_price else limit_price
        return None
    if bar_high >= limit_price:
        return max(limit_price, bar_open) if bar_open >= limit_price else limit_price
    return None


def apply_slippage(price: float, direction: str, is_entry: bool, bps: float = SLIPPAGE_BPS) -> float:
    """Slippage always moves the fill against the trader. A LONG entry or a
    SHORT exit is a buy (fills higher); a SHORT entry or a LONG exit is a sell
    (fills lower)."""
    is_buy = (direction == "LONG") == is_entry
    factor = 1.0 + bps / 10_000.0 if is_buy else 1.0 - bps / 10_000.0
    return price * factor
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_broker_sim.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/backtest/broker_sim.py tests/unit/test_broker_sim.py
git commit -m "M6 Task 2: order fill simulation (backtest/broker_sim.py)"
```

---

## Task 3: `algo/long.py` — long entry qualification + exit-signal series

**Files:**
- Create: `src/rs_spy/algo/long.py`
- Test: `tests/unit/test_long_algo.py`

**Interfaces:**
- Consumes: `rs_spy.indicators.candle_structure.{chop_ratio, stacked_count}`,
  `rs_spy.bias.buckets.{BEAR, STRONG_BEAR}`. Reads a `features` DataFrame with the
  same columns `selection.features_m5.compute_symbol_features_m5` produces
  (`rolling_rrs_m5`, `close`, `vwap_m5`, `rvol_m5`, `lrsi_m5`) and a `df_m5` OHLCV
  DataFrame sharing that same index.
- Produces (used by Task 6):
  - `not_extended_long(close: pd.Series, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series`
  - `confirm_trigger_entry_long(features: pd.DataFrame, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series`
  - `dip_quality_pass_long(df_m5: pd.DataFrame, features: pd.DataFrame, atr_m5: pd.Series) -> pd.Series`
  - `rs_failure_long(rolling_rrs_m5: pd.Series) -> pd.Series`
  - `vwap_loss_long(close: pd.Series, vwap_m5: pd.Series) -> pd.Series`
  - `momentum_stall_long(lrsi_m5: pd.Series) -> pd.Series`
  - `market_flip_exit_long(bias: pd.Series, flip_flatten: pd.Series) -> pd.Series`
  - Constants: `PROFIT_TARGET_ATR_MULT = 1.0`, `CHOP_PROFIT_TARGET_MULT = 0.75`,
    `TRAIL_TRIGGER_ATR_MULT = 1.5`, `TRAIL_STOP_ATR_MULT = 0.25`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_long_algo.py
import pandas as pd
import pytest

from rs_spy.algo.long import (
    confirm_trigger_entry_long,
    dip_quality_pass_long,
    market_flip_exit_long,
    momentum_stall_long,
    not_extended_long,
    rs_failure_long,
    vwap_loss_long,
)
from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR


def _idx(n):
    return pd.date_range("2026-01-05 14:30", periods=n, freq="5min", tz="UTC")


def test_not_extended_long():
    close = pd.Series([102.0, 105.0], index=_idx(2))
    ema8 = pd.Series([100.0, 100.0], index=_idx(2))
    atr = pd.Series([5.0, 5.0], index=_idx(2))
    result = not_extended_long(close, ema8, atr)
    assert result.iloc[0]  # 102-100=2 <= 5
    assert result.iloc[1]  # 105-100=5 <= 5 (boundary, inclusive)


def test_confirm_trigger_entry_long_requires_all_three_conditions():
    idx = _idx(1)
    features = pd.DataFrame({"rolling_rrs_m5": [1.2], "close": [101.0], "vwap_m5": [100.0]}, index=idx)
    ema8 = pd.Series([99.0], index=idx)
    atr = pd.Series([5.0], index=idx)
    assert confirm_trigger_entry_long(features, ema8, atr).iloc[0]

    features_weak_rrs = features.assign(rolling_rrs_m5=[0.5])
    assert not confirm_trigger_entry_long(features_weak_rrs, ema8, atr).iloc[0]

    features_below_vwap = features.assign(close=[99.0])
    assert not confirm_trigger_entry_long(features_below_vwap, ema8, atr).iloc[0]

    atr_tiny = pd.Series([0.5], index=idx)  # close-ema8=2 > 1.0*0.5 -> extended
    assert not confirm_trigger_entry_long(features, ema8, atr_tiny).iloc[0]


def test_dip_quality_pass_long_passes_a_healthy_mixed_low_volume_pullback():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [110, 109, 108.5, 108, 108.2, 108.5],
            "high": [111, 109.5, 109, 108.5, 108.8, 109],
            "low": [109, 108, 107.5, 107.5, 107.8, 108],
            "close": [109.5, 108.5, 108, 108.3, 108.5, 108.8],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame(
        {"rvol_m5": [0.6, 0.6, 0.5, 0.6, 0.6, 0.6], "vwap_m5": [107.0] * 6},
        index=idx,
    )
    atr = pd.Series([2.0] * 6, index=idx)
    result = dip_quality_pass_long(df_m5, features, atr)
    assert result.iloc[-1]


def test_dip_quality_pass_long_fails_on_stacked_red_heavy_volume():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [110, 109, 108, 107, 106, 105],
            "high": [110.1, 109.1, 108.1, 107.1, 106.1, 105.1],
            "low": [108.9, 107.9, 106.9, 105.9, 104.9, 103.9],
            "close": [109, 108, 107, 106, 105, 104],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [2.0] * 6, "vwap_m5": [107.0] * 6}, index=idx)
    atr = pd.Series([1.0] * 6, index=idx)
    result = dip_quality_pass_long(df_m5, features, atr)
    assert not result.iloc[-1]


def test_rs_failure_long_requires_two_consecutive_negative_bars():
    rrs = pd.Series([1.0, -0.5, -0.2, 0.1], index=_idx(4))
    result = rs_failure_long(rrs)
    assert list(result) == [False, False, True, False]


def test_vwap_loss_long_requires_two_consecutive_closes_below():
    close = pd.Series([101.0, 99.0, 98.0, 102.0], index=_idx(4))
    vwap = pd.Series([100.0] * 4, index=_idx(4))
    result = vwap_loss_long(close, vwap)
    assert list(result) == [False, False, True, False]


def test_momentum_stall_long_fires_on_cross_down_through_80():
    lrsi = pd.Series([75.0, 85.0, 78.0, 90.0], index=_idx(4))
    result = momentum_stall_long(lrsi)
    assert list(result) == [False, False, True, False]


def test_market_flip_exit_long_only_on_down_flip():
    idx = _idx(2)
    bias = pd.Series([BULL, BEAR], index=idx)
    flip = pd.Series([False, True], index=idx)
    result = market_flip_exit_long(bias, flip)
    assert list(result) == [False, True]

    bias_up = pd.Series([BEAR, BULL], index=idx)
    flip_up = pd.Series([False, True], index=idx)
    result_up = market_flip_exit_long(bias_up, flip_up)
    assert list(result_up) == [False, False]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_long_algo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.algo.long'`

- [ ] **Step 3: Write the implementation**

```python
# src/rs_spy/algo/long.py
"""Long-bias entry qualification + stateless exit-signal series. algo-spec/05.

Entry path A (05 §2, trigger day) and path B (05 §3, dip re-entry) both funnel
through selection.watchlist's state machine reaching ENTRY_EVAL (see
backtest/engine_m5.py, Task 6); this module supplies the bar-close reconfirmation
checks each path requires before an order is actually submitted
(confirm_trigger_entry_long for path A, dip_quality_pass_long for path B), plus the
position-management rule set (05 §4) as stateless per-bar boolean Series -- the
stateful pieces (does this position exist, what's its entry price/stop, has the
first-fired rule already closed it) live in the event loop, matching the style
selection/gates.py already uses for D1.

Dip quality (05 §3's "PASS if mixed overlapping candles, RVOL(pullback) < 1.0,
depth <= 1.5xATR below the local high, VWAP held") is a discretionary, prose
description with no precise formula -- translated here into the project's existing
indicator vocabulary, the same kind of disclosed translation as
bias/daily_context.py's suspect_rally breakout audit: "mixed overlapping candles"
-> candle_structure.chop_ratio over the pullback window >= MIXED_CHOP_MIN;
"RVOL(pullback) < 1.0" -> mean rvol_m5 over the window; "depth" -> the rolling high
over the window minus the current low, in ATR units; "VWAP held" -> close stayed
above vwap_m5 for the whole window. FAIL ("stacked red candles or heavy-volume
drop") maps to candle_structure.stacked_count reaching <= -STACK_FAIL_COUNT
anywhere in the window, which excludes the pass regardless of the other checks.
"""
import pandas as pd

from rs_spy.bias.buckets import BEAR, STRONG_BEAR
from rs_spy.indicators.candle_structure import chop_ratio, stacked_count

NOT_EXTENDED_ATR_MULT = 1.0
DIP_PULLBACK_WINDOW = 6  # M5 bars (~30 min) considered for the dip-quality read
DIP_DEPTH_ATR_MULT = 1.5
MIXED_CHOP_MIN = 0.4
STACK_FAIL_COUNT = 3
LRSI_STALL_LEVEL = 80.0
PROFIT_TARGET_ATR_MULT = 1.0
CHOP_PROFIT_TARGET_MULT = 0.75
TRAIL_TRIGGER_ATR_MULT = 1.5
TRAIL_STOP_ATR_MULT = 0.25


def not_extended_long(close: pd.Series, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    return (close - ema8) <= NOT_EXTENDED_ATR_MULT * atr_m5


def confirm_trigger_entry_long(features: pd.DataFrame, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    """05 §2's trigger-bar reconfirmation: RollingRRS_M5 >= 1.0 still true,
    above VWAP, not extended."""
    return (
        (features["rolling_rrs_m5"] >= 1.0)
        & (features["close"] > features["vwap_m5"])
        & not_extended_long(features["close"], ema8, atr_m5)
    )


def dip_quality_pass_long(df_m5: pd.DataFrame, features: pd.DataFrame, atr_m5: pd.Series) -> pd.Series:
    window = DIP_PULLBACK_WINDOW
    cr = chop_ratio(df_m5, window=window)
    sc = stacked_count(df_m5, volume_ratio=features["rvol_m5"])
    rvol_avg = features["rvol_m5"].rolling(window).mean()
    local_high = df_m5["high"].rolling(window).max()
    depth = (local_high - df_m5["low"]) / atr_m5
    vwap_held = (df_m5["close"] > features["vwap_m5"]).rolling(window).min().astype(bool)
    stacked_red_fail = sc.rolling(window).min() <= -STACK_FAIL_COUNT

    passes = (cr >= MIXED_CHOP_MIN) & (rvol_avg < 1.0) & (depth <= DIP_DEPTH_ATR_MULT) & vwap_held & ~stacked_red_fail
    return passes.fillna(False)


def rs_failure_long(rolling_rrs_m5: pd.Series) -> pd.Series:
    """05 §4.3: RollingRRS_M5 < 0 for 2 consecutive bars."""
    below = rolling_rrs_m5 < 0
    return below & below.shift(1, fill_value=False)


def vwap_loss_long(close: pd.Series, vwap_m5: pd.Series) -> pd.Series:
    """05 §4.4: 2 consecutive M5 closes below VWAP."""
    below = close < vwap_m5
    return below & below.shift(1, fill_value=False)


def momentum_stall_long(lrsi_m5: pd.Series) -> pd.Series:
    """05 §4.5: LRSI crosses down through 80."""
    return (lrsi_m5.shift(1) >= LRSI_STALL_LEVEL) & (lrsi_m5 < LRSI_STALL_LEVEL)


def market_flip_exit_long(bias: pd.Series, flip_flatten: pd.Series) -> pd.Series:
    """05 §4.2: bias -> BEAR/STRONG_BEAR with stacked-red/RVOL confirmation.
    bias/engine.py's flip_flatten already encodes that stack+RVOL confirmation
    symmetrically for both flip directions -- restrict to the down-flip here."""
    return flip_flatten & bias.isin([BEAR, STRONG_BEAR])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_long_algo.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/algo/long.py tests/unit/test_long_algo.py
git commit -m "M6 Task 3: long entry qualification + exit-signal series (algo/long.py)"
```

---

## Task 4: `algo/short.py` — short entry qualification + exit-signal series + squeeze guard

**Files:**
- Create: `src/rs_spy/algo/short.py`
- Test: `tests/unit/test_short_algo.py`

**Interfaces:**
- Consumes: `rs_spy.bias.buckets.{BULL, STRONG_BULL}`,
  `rs_spy.indicators.candle_structure.{chop_ratio, stacked_count}`. Same
  `features`/`df_m5` shape as Task 3.
- Produces (used by Task 6):
  - `not_extended_short(close, ema8, atr_m5) -> pd.Series`
  - `confirm_trigger_entry_short(features, ema8, atr_m5) -> pd.Series`
  - `bounce_quality_pass_short(df_m5, features, atr_m5) -> pd.Series`
  - `rs_failure_short(rolling_rrs_m5) -> pd.Series`
  - `vwap_loss_short(close, vwap_m5) -> pd.Series`
  - `momentum_stall_short(lrsi_m5) -> pd.Series`
  - `market_flip_exit_short(bias: pd.Series) -> pd.Series` (no `flip_flatten` arg —
    see docstring: this flip is unconditional per 06 §4)
  - `squeeze_guard_short(bar_high: pd.Series, prev_close: pd.Series, atr_m5: pd.Series, rvol_m5: pd.Series) -> pd.Series`
  - Constants mirror Task 3's, plus `SQUEEZE_ATR_MULT = 2.0`, `SQUEEZE_RVOL_MULT = 2.0`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_short_algo.py
import pandas as pd
import pytest

from rs_spy.algo.short import (
    bounce_quality_pass_short,
    confirm_trigger_entry_short,
    market_flip_exit_short,
    momentum_stall_short,
    not_extended_short,
    rs_failure_short,
    squeeze_guard_short,
    vwap_loss_short,
)
from rs_spy.bias.buckets import BEAR, BULL


def _idx(n):
    return pd.date_range("2026-01-05 14:30", periods=n, freq="5min", tz="UTC")


def test_not_extended_short():
    close = pd.Series([98.0, 95.0], index=_idx(2))
    ema8 = pd.Series([100.0, 100.0], index=_idx(2))
    atr = pd.Series([5.0, 5.0], index=_idx(2))
    result = not_extended_short(close, ema8, atr)
    assert result.iloc[0]  # 100-98=2 <= 5
    assert result.iloc[1]  # 100-95=5 <= 5


def test_confirm_trigger_entry_short_requires_all_three_conditions():
    idx = _idx(1)
    features = pd.DataFrame({"rolling_rrs_m5": [-1.2], "close": [99.0], "vwap_m5": [100.0]}, index=idx)
    ema8 = pd.Series([101.0], index=idx)
    atr = pd.Series([5.0], index=idx)
    assert confirm_trigger_entry_short(features, ema8, atr).iloc[0]

    weak_rrs = features.assign(rolling_rrs_m5=[-0.5])
    assert not confirm_trigger_entry_short(weak_rrs, ema8, atr).iloc[0]

    above_vwap = features.assign(close=[101.0])
    assert not confirm_trigger_entry_short(above_vwap, ema8, atr).iloc[0]


def test_bounce_quality_pass_short_passes_a_wimpy_low_volume_bounce():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [90, 91, 91.5, 92, 91.8, 91.5],
            "high": [91, 92, 92.5, 92.5, 92.2, 92],
            "low": [89, 90.5, 91, 91.5, 91.2, 91],
            "close": [90.5, 91.5, 92, 91.8, 91.6, 91.3],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [0.6] * 6, "vwap_m5": [93.0] * 6}, index=idx)
    atr = pd.Series([2.0] * 6, index=idx)
    result = bounce_quality_pass_short(df_m5, features, atr)
    assert result.iloc[-1]


def test_bounce_quality_pass_short_fails_on_stacked_green_heavy_volume():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [90, 91, 92, 93, 94, 95],
            "high": [91.1, 92.1, 93.1, 94.1, 95.1, 96.1],
            "low": [89.9, 90.9, 91.9, 92.9, 93.9, 94.9],
            "close": [91, 92, 93, 94, 95, 96],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [2.0] * 6, "vwap_m5": [93.0] * 6}, index=idx)
    atr = pd.Series([1.0] * 6, index=idx)
    result = bounce_quality_pass_short(df_m5, features, atr)
    assert not result.iloc[-1]


def test_rs_failure_short_requires_two_consecutive_positive_bars():
    rrs = pd.Series([-1.0, 0.5, 0.2, -0.1], index=_idx(4))
    assert list(rs_failure_short(rrs)) == [False, False, True, False]


def test_vwap_loss_short_requires_two_consecutive_closes_above():
    close = pd.Series([99.0, 101.0, 102.0, 98.0], index=_idx(4))
    vwap = pd.Series([100.0] * 4, index=_idx(4))
    assert list(vwap_loss_short(close, vwap)) == [False, False, True, False]


def test_momentum_stall_short_fires_on_cross_up_through_20():
    lrsi = pd.Series([25.0, 15.0, 22.0, 10.0], index=_idx(4))
    assert list(momentum_stall_short(lrsi)) == [False, False, True, False]


def test_market_flip_exit_short_unconditional_on_bull_flip():
    bias = pd.Series([BEAR, BULL], index=_idx(2))
    assert list(market_flip_exit_short(bias)) == [False, True]


def test_squeeze_guard_short_fires_on_violent_adverse_spike():
    high = pd.Series([102.5], index=_idx(1))
    prev_close = pd.Series([100.0], index=_idx(1))
    atr = pd.Series([1.0], index=_idx(1))
    rvol = pd.Series([2.5], index=_idx(1))
    assert squeeze_guard_short(high, prev_close, atr, rvol).iloc[0]

    rvol_low = pd.Series([1.0], index=_idx(1))
    assert not squeeze_guard_short(high, prev_close, atr, rvol_low).iloc[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_short_algo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.algo.short'`

- [ ] **Step 3: Write the implementation**

```python
# src/rs_spy/algo/short.py
"""Short-bias entry qualification + stateless exit-signal series. algo-spec/06.

Mirror of algo/long.py -- see that module's docstring for the dip/bounce-quality
translation methodology (identical here, mirrored to the downside). Two real
asymmetries vs. the long side, both directly from spec text, not omissions:
market_flip_exit_short is UNCONDITIONAL (06 §4: "exit all shorts at market -- not
merely tightened -- asymmetric vs the long side because upside squeezes are
faster"), unlike the long side's stacked/RVOL-confirmed flip; and there's an
additional squeeze_guard_short with no long-side equivalent (06 §4: "any M5 bar
against the position >= 2.0xATR on RVOL >= 2.0 -> exit immediately regardless of
RRS").
"""
import pandas as pd

from rs_spy.bias.buckets import BULL, STRONG_BULL
from rs_spy.indicators.candle_structure import chop_ratio, stacked_count

NOT_EXTENDED_ATR_MULT = 1.0
DIP_PULLBACK_WINDOW = 6
DIP_DEPTH_ATR_MULT = 1.5
MIXED_CHOP_MIN = 0.4
STACK_FAIL_COUNT = 3
LRSI_STALL_LEVEL = 20.0
PROFIT_TARGET_ATR_MULT = 1.0
CHOP_PROFIT_TARGET_MULT = 0.75
TRAIL_TRIGGER_ATR_MULT = 1.5
TRAIL_STOP_ATR_MULT = 0.25
SQUEEZE_ATR_MULT = 2.0
SQUEEZE_RVOL_MULT = 2.0


def not_extended_short(close: pd.Series, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    return (ema8 - close) <= NOT_EXTENDED_ATR_MULT * atr_m5


def confirm_trigger_entry_short(features: pd.DataFrame, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    """06 §2's trigger-bar reconfirmation: RollingRRS_M5 <= -1.0 still true,
    below VWAP, not extended."""
    return (
        (features["rolling_rrs_m5"] <= -1.0)
        & (features["close"] < features["vwap_m5"])
        & not_extended_short(features["close"], ema8, atr_m5)
    )


def bounce_quality_pass_short(df_m5: pd.DataFrame, features: pd.DataFrame, atr_m5: pd.Series) -> pd.Series:
    window = DIP_PULLBACK_WINDOW
    cr = chop_ratio(df_m5, window=window)
    sc = stacked_count(df_m5, volume_ratio=features["rvol_m5"])
    rvol_avg = features["rvol_m5"].rolling(window).mean()
    local_low = df_m5["low"].rolling(window).min()
    depth = (df_m5["high"] - local_low) / atr_m5
    vwap_held = (df_m5["close"] < features["vwap_m5"]).rolling(window).min().astype(bool)
    stacked_green_fail = sc.rolling(window).max() >= STACK_FAIL_COUNT

    passes = (cr >= MIXED_CHOP_MIN) & (rvol_avg < 1.0) & (depth <= DIP_DEPTH_ATR_MULT) & vwap_held & ~stacked_green_fail
    return passes.fillna(False)


def rs_failure_short(rolling_rrs_m5: pd.Series) -> pd.Series:
    """06 §4: RollingRRS_M5 > 0 for 2 consecutive bars."""
    above = rolling_rrs_m5 > 0
    return above & above.shift(1, fill_value=False)


def vwap_loss_short(close: pd.Series, vwap_m5: pd.Series) -> pd.Series:
    above = close > vwap_m5
    return above & above.shift(1, fill_value=False)


def momentum_stall_short(lrsi_m5: pd.Series) -> pd.Series:
    """06 §4: LRSI crosses up through 20."""
    return (lrsi_m5.shift(1) <= LRSI_STALL_LEVEL) & (lrsi_m5 > LRSI_STALL_LEVEL)


def market_flip_exit_short(bias: pd.Series) -> pd.Series:
    return bias.isin([BULL, STRONG_BULL])


def squeeze_guard_short(bar_high: pd.Series, prev_close: pd.Series, atr_m5: pd.Series, rvol_m5: pd.Series) -> pd.Series:
    adverse_move = bar_high - prev_close
    return (adverse_move >= SQUEEZE_ATR_MULT * atr_m5) & (rvol_m5 >= SQUEEZE_RVOL_MULT)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_short_algo.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/algo/short.py tests/unit/test_short_algo.py
git commit -m "M6 Task 4: short entry qualification + exit-signal series (algo/short.py)"
```

---

## Task 5: `backtest/engine_m5.py` part 1 — `_prepare_m5` precompute layer

**Files:**
- Create: `src/rs_spy/backtest/engine_m5.py`
- Test: `tests/unit/test_engine_m5_backtest.py`

**Interfaces:**
- Consumes:
  - `rs_spy.bias.engine.bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5) -> pd.DataFrame`
    with columns `raw_score, smoothed_score, bias, trigger, warmup, flip_flatten`
  - `rs_spy.bias.daily_context.daily_context_series(spy_d1) -> pd.DataFrame` with a
    `regime_d1` column
  - `rs_spy.data.resample.align_daily_to_intraday(daily: pd.Series, intraday_index, shift=1) -> pd.Series`
  - `rs_spy.selection.features_m5.compute_symbol_features_m5(df_m1, df_m5, df_d1, spy_m1, spy_m5, spy_d1, qqq_m5=None, rrs_window=12) -> pd.DataFrame`
  - `rs_spy.selection.gates.gates_pass_long_m5(df, features, earnings_blackout=None, ..., disabled=frozenset()) -> pd.Series`
    and `gates_pass_short_m5` (same shape, see `src/rs_spy/selection/gates.py:175-235`)
  - `rs_spy.selection.scoring.score_long_m5(features) -> pd.Series` and `score_short_m5`
  - `rs_spy.indicators.atr.atr(df, n) -> pd.Series`
  - `rs_spy.algo.long`/`rs_spy.algo.short`'s signal functions from Tasks 3/4
- Produces (used by Task 6):
  - `@dataclass class PreparedM5` with the fields listed in Step 3 below
  - `_prepare_m5(universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5, sectors, earnings_blackout=None, config=None) -> PreparedM5`
    (leading underscore: internal to this module, called by `run_m5_backtest` in
    Task 6, which lives in the same file)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_engine_m5_backtest.py
import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5


def _m1_session(date: str, n_minutes: int, start_price: float, drift: float, seed: int) -> pd.DataFrame:
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


def _build_d1(m1: pd.DataFrame) -> pd.DataFrame:
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_localize(None)
    return daily


DATES = [f"2026-02-{2 + i:02d}" for i in range(30)]  # 30 trading-day-like sessions


@pytest.fixture
def universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    qqq_m1 = _build_m1(DATES, drift=0.0006, seed=2)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)

    spy_m5 = resample_ohlcv(spy_m1, "5min")
    qqq_m5 = resample_ohlcv(qqq_m1, "5min")
    aapl_m5 = resample_ohlcv(aapl_m1, "5min")

    spy_d1 = _build_d1(spy_m1)
    qqq_d1 = _build_d1(qqq_m1)
    aapl_d1 = _build_d1(aapl_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "qqq_m1": qqq_m1, "qqq_m5": qqq_m5, "qqq_d1": qqq_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
    }


def test_prepare_m5_returns_calendar_matching_spy_m5_index(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.calendar.equals(universe["spy_m5"].index)
    assert "raw_score" in prepared.bias_df.columns
    assert "regime_d1_m5" not in prepared.bias_df.columns  # lives on PreparedM5 directly


def test_prepare_m5_per_symbol_outputs_are_reindexed_onto_the_master_calendar(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    for attr in ("bars", "features", "ema8", "atr_m5", "adv20_m5", "gate_long", "gate_short",
                 "score_long", "score_short", "rs_failure_long", "rs_failure_short",
                 "vwap_loss_long", "vwap_loss_short", "momentum_stall_long", "momentum_stall_short",
                 "confirm_trigger_long", "confirm_trigger_short", "dip_quality_long",
                 "bounce_quality_short", "squeeze_guard_short"):
        series_or_df = getattr(prepared, attr)["AAPL"]
        assert series_or_df.index.equals(prepared.calendar), f"{attr} not reindexed onto master calendar"


def test_prepare_m5_gates_are_bool_dtype_with_no_nan_after_reindex(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    gl = prepared.gate_long["AAPL"]
    assert gl.dtype == bool
    assert not gl.isna().any()


def test_prepare_m5_regime_d1_m5_is_a_single_market_wide_series(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.regime_d1_m5.index.equals(prepared.calendar)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_engine_m5_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_spy.backtest.engine_m5'`

- [ ] **Step 3: Write the implementation**

```python
# src/rs_spy/backtest/engine_m5.py
"""M5-cadence event-driven backtest engine. algo-spec/05, 06, 07 (M5-adapted).

Mirrors backtest/engine.py's own two-phase shape (precompute, then a single
chronological event loop) one cadence level up. This file is split into two
halves for reviewability: _prepare_m5 (this task) runs every M5-cadence
indicator/gate/score function once per symbol over its own full history; Part 2
(run_m5_backtest, added in the same file by a later task) drives the bar-by-bar
event loop that consumes PreparedM5's output.

Master calendar = SPY's own M5 bar index for the whole backtest window. Unlike
backtest/engine.py's D1 skeleton (which intersects every symbol's calendar --
fine at D1 density, since daily bars rarely have gaps), M5-cadence coverage
density varies hugely across the curated universe on Alpaca's IEX-only feed (see
IMPLEMENTATION.md's rvol.py deviation -- some symbols have a bar for only ~20% of
RTH minutes). Intersecting 130 symbols' M5 indices at that density would produce
a near-empty calendar. Instead, every symbol's per-bar outputs are computed on
its OWN native M5 index first, then reindexed onto the shared master calendar
(strict reindex, no ffill -- see this plan's Global Constraints section): a
master-calendar bar a thin symbol has no native bar for reads as "no signal"
(NaN/False), which every downstream gate/entry check already treats as "fails",
by construction (NaN comparisons are False in pandas).
"""
from dataclasses import dataclass, field

import pandas as pd

from rs_spy.algo import long as long_algo
from rs_spy.algo import short as short_algo
from rs_spy.bias.daily_context import daily_context_series
from rs_spy.bias.engine import bias_series
from rs_spy.data.resample import align_daily_to_intraday
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.selection import gates, scoring
from rs_spy.selection.features_m5 import RRS_M5_WINDOW, compute_symbol_features_m5

ATR_PERIOD_M5 = 14
EMA8_SPAN = 8
ADV_LOOKBACK_DAYS = 20


@dataclass
class BacktestConfigM5:
    risk_per_trade_pct: float = 0.005
    max_concurrent_long: int = 5
    max_concurrent_short: int = 3
    short_size_multiplier: float = 0.75
    min_list_score: float = 50.0
    min_hold_score: float = 40.0
    top_n_list: int = 20
    top_n_tradeable: int = 5
    max_per_sector: int = 2
    shorts_enabled: bool = False
    starting_equity: float = 100_000.0
    min_adv_shares: float = 50_000.0
    disabled_gates: frozenset = field(default_factory=frozenset)
    rrs_m5_window: int = RRS_M5_WINDOW
    use_qqq_crosscheck: bool = False
    max_entries_per_symbol_long: int = 2
    max_entries_per_symbol_short: int = 1
    expected_hold_minutes: float = 120.0
    unfilled_cancel_bars: int = 2


@dataclass
class PreparedM5:
    calendar: pd.DatetimeIndex
    bias_df: pd.DataFrame
    regime_d1_m5: pd.Series
    bars: dict
    features: dict
    ema8: dict
    atr_m5: dict
    adv20_m5: dict
    gate_long: dict
    gate_short: dict
    score_long: dict
    score_short: dict
    rs_failure_long: dict
    rs_failure_short: dict
    vwap_loss_long: dict
    vwap_loss_short: dict
    momentum_stall_long: dict
    momentum_stall_short: dict
    confirm_trigger_long: dict
    confirm_trigger_short: dict
    dip_quality_long: dict
    bounce_quality_short: dict
    squeeze_guard_short: dict


def _prepare_m5(
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
) -> PreparedM5:
    config = config or BacktestConfigM5()
    earnings_blackout = earnings_blackout or {}
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    calendar = bias_df.index

    daily_ctx = daily_context_series(spy_d1)
    regime_d1_m5 = align_daily_to_intraday(daily_ctx["regime_d1"], calendar)

    bars, features, ema8, atr_m5, adv20_m5 = {}, {}, {}, {}, {}
    gate_long, gate_short, score_long, score_short = {}, {}, {}, {}
    rs_failure_long, rs_failure_short = {}, {}
    vwap_loss_long, vwap_loss_short = {}, {}
    momentum_stall_long, momentum_stall_short = {}, {}
    confirm_trigger_long, confirm_trigger_short = {}, {}
    dip_quality_long, bounce_quality_short, squeeze_guard_short = {}, {}, {}

    for sym, df_m5_native in universe_m5.items():
        df_m1_native = universe_m1[sym]
        df_d1_native = universe_d1[sym]

        feat_native = compute_symbol_features_m5(
            df_m1_native, df_m5_native, df_d1_native, spy_m1, spy_m5, spy_d1,
            qqq_m5=qqq_m5 if config.use_qqq_crosscheck else None,
            rrs_window=config.rrs_m5_window,
        )
        atr_native = atr_fn(df_m5_native, n=ATR_PERIOD_M5)
        ema8_native = df_m5_native["close"].ewm(span=EMA8_SPAN, adjust=False).mean()
        adv20_daily = df_d1_native["volume"].rolling(ADV_LOOKBACK_DAYS).mean()
        adv20_native = align_daily_to_intraday(adv20_daily, df_m5_native.index)

        gl_native = gates.gates_pass_long_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
        ).fillna(False)
        gs_native = gates.gates_pass_short_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
        ).fillna(False)
        sl_native = scoring.score_long_m5(feat_native)
        ss_native = scoring.score_short_m5(feat_native)

        rs_fail_l_native = long_algo.rs_failure_long(feat_native["rolling_rrs_m5"])
        rs_fail_s_native = short_algo.rs_failure_short(feat_native["rolling_rrs_m5"])
        vwap_l_native = long_algo.vwap_loss_long(feat_native["close"], feat_native["vwap_m5"])
        vwap_s_native = short_algo.vwap_loss_short(feat_native["close"], feat_native["vwap_m5"])
        stall_l_native = long_algo.momentum_stall_long(feat_native["lrsi_m5"])
        stall_s_native = short_algo.momentum_stall_short(feat_native["lrsi_m5"])
        confirm_l_native = long_algo.confirm_trigger_entry_long(feat_native, ema8_native, atr_native)
        confirm_s_native = short_algo.confirm_trigger_entry_short(feat_native, ema8_native, atr_native)
        dip_l_native = long_algo.dip_quality_pass_long(df_m5_native, feat_native, atr_native)
        bounce_s_native = short_algo.bounce_quality_pass_short(df_m5_native, feat_native, atr_native)
        squeeze_s_native = short_algo.squeeze_guard_short(
            df_m5_native["high"], df_m5_native["close"].shift(1), atr_native, feat_native["rvol_m5"]
        )

        bars[sym] = df_m5_native.reindex(calendar)
        features[sym] = feat_native.reindex(calendar)
        ema8[sym] = ema8_native.reindex(calendar)
        atr_m5[sym] = atr_native.reindex(calendar)
        adv20_m5[sym] = adv20_native.reindex(calendar)
        gate_long[sym] = gl_native.reindex(calendar, fill_value=False)
        gate_short[sym] = gs_native.reindex(calendar, fill_value=False)
        score_long[sym] = sl_native.reindex(calendar)
        score_short[sym] = ss_native.reindex(calendar)
        rs_failure_long[sym] = rs_fail_l_native.reindex(calendar, fill_value=False)
        rs_failure_short[sym] = rs_fail_s_native.reindex(calendar, fill_value=False)
        vwap_loss_long[sym] = vwap_l_native.reindex(calendar, fill_value=False)
        vwap_loss_short[sym] = vwap_s_native.reindex(calendar, fill_value=False)
        momentum_stall_long[sym] = stall_l_native.reindex(calendar, fill_value=False)
        momentum_stall_short[sym] = stall_s_native.reindex(calendar, fill_value=False)
        confirm_trigger_long[sym] = confirm_l_native.reindex(calendar, fill_value=False)
        confirm_trigger_short[sym] = confirm_s_native.reindex(calendar, fill_value=False)
        dip_quality_long[sym] = dip_l_native.reindex(calendar, fill_value=False)
        bounce_quality_short[sym] = bounce_s_native.reindex(calendar, fill_value=False)
        squeeze_guard_short[sym] = squeeze_s_native.reindex(calendar, fill_value=False)

    return PreparedM5(
        calendar=calendar,
        bias_df=bias_df,
        regime_d1_m5=regime_d1_m5,
        bars=bars,
        features=features,
        ema8=ema8,
        atr_m5=atr_m5,
        adv20_m5=adv20_m5,
        gate_long=gate_long,
        gate_short=gate_short,
        score_long=score_long,
        score_short=score_short,
        rs_failure_long=rs_failure_long,
        rs_failure_short=rs_failure_short,
        vwap_loss_long=vwap_loss_long,
        vwap_loss_short=vwap_loss_short,
        momentum_stall_long=momentum_stall_long,
        momentum_stall_short=momentum_stall_short,
        confirm_trigger_long=confirm_trigger_long,
        confirm_trigger_short=confirm_trigger_short,
        dip_quality_long=dip_quality_long,
        bounce_quality_short=bounce_quality_short,
        squeeze_guard_short=squeeze_guard_short,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_engine_m5_backtest.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Also run the full existing suite to confirm no regressions**

Run: `pytest -q`
Expected: all prior tests plus these 4 pass (no changes to any existing file in
this task)

- [ ] **Step 6: Commit**

```bash
git add src/rs_spy/backtest/engine_m5.py tests/unit/test_engine_m5_backtest.py
git commit -m "M6 Task 5: M5 backtest precompute layer (backtest/engine_m5.py, _prepare_m5)"
```

---

## Task 6: `backtest/engine_m5.py` part 2 — `run_m5_backtest` event loop

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py` (append to the file created in Task 5
  — do not touch `_prepare_m5`, `PreparedM5`, or `BacktestConfigM5`)
- Test: `tests/unit/test_engine_m5_backtest.py` (append new tests to the file
  created in Task 5)

**Interfaces:**
- Consumes: `PreparedM5`/`BacktestConfigM5`/`_prepare_m5` from Task 5;
  `rs_spy.algo.risk` (`RiskManager`, `stop_price_long/short`,
  `neutral_tighten_stop_long/short`, `position_size`, `cap_shares`) from Task 1;
  `rs_spy.backtest.broker_sim` (`entry_limit_price`, `try_fill_entry`,
  `apply_slippage`) from Task 2; `rs_spy.algo.long`/`rs_spy.algo.short`'s
  `market_flip_exit_long/short`, `PROFIT_TARGET_ATR_MULT`,
  `CHOP_PROFIT_TARGET_MULT`, `TRAIL_TRIGGER_ATR_MULT`, `TRAIL_STOP_ATR_MULT` from
  Tasks 3/4; `rs_spy.selection.watchlist` (`IDLE, QUALIFIED, DIP_ARMED, ENTRY_EVAL,
  next_state_long, next_state_short, build_tradeable_list, apply_trigger_bypass`);
  `rs_spy.bias.buckets` (`BULL, STRONG_BULL, BEAR, STRONG_BEAR, NEUTRAL,
  LONG_TRIGGER, SHORT_TRIGGER`); `rs_spy.bias.regime.TREND_UP`.
- Produces (used by Task 7):
  - `@dataclass class PositionM5` (fields: `symbol, direction, entry_bar,
    entry_price, shares, stop, entry_atr, entries_today_key, peak_favorable`)
  - `@dataclass class TradeM5` (fields: `symbol, direction, entry_time,
    entry_price, exit_time, exit_price, shares, exit_reason, pnl, r_multiple`)
  - `@dataclass class BacktestResultM5` with `.trades: list[TradeM5]`,
    `.equity_curve: pd.Series | None`, and `.trades_df() -> pd.DataFrame`
  - `run_m5_backtest(universe_m1, universe_m5, universe_d1, spy_m1, spy_m5,
    spy_d1, qqq_m1, qqq_m5, sectors, earnings_blackout=None, config=None) ->
    BacktestResultM5`

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/unit/test_engine_m5_backtest.py
from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest


def test_run_m5_backtest_produces_a_trade_log_and_equity_curve(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert result.equity_curve is not None
    assert len(result.equity_curve) > 0
    trades_df = result.trades_df()
    if not trades_df.empty:
        assert set(trades_df["exit_reason"].unique()) <= {
            "hard_stop", "market_flip", "rs_failure", "vwap_loss",
            "profit_take", "time_flat", "squeeze_guard",
        }
        assert (trades_df["shares"] > 0).all()


def test_run_m5_backtest_never_exceeds_max_concurrent_long(universe):
    config = BacktestConfigM5(max_concurrent_long=1)
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=config,
    )
    trades_df = result.trades_df()
    if trades_df.empty:
        return
    events = []
    for _, t in trades_df.iterrows():
        events.append((t["entry_time"], 1))
        events.append((t["exit_time"], -1))
    events.sort()
    concurrent = 0
    for _, delta in events:
        concurrent += delta
        assert concurrent <= 1


def test_run_m5_backtest_shorts_disabled_by_default_produces_no_short_trades(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    if not trades_df.empty:
        assert (trades_df["direction"] == "LONG").all()


def test_run_m5_backtest_no_new_entries_before_1015_or_after_1530_et(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    if trades_df.empty:
        return
    et_times = trades_df["entry_time"].dt.tz_convert("America/New_York")
    tod = et_times - et_times.dt.normalize()
    assert (tod >= pd.Timedelta(hours=10, minutes=15)).all()
    assert (tod <= pd.Timedelta(hours=15, minutes=30)).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_engine_m5_backtest.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_m5_backtest'`

- [ ] **Step 3: Append the event-loop implementation**

```python
# appended to src/rs_spy/backtest/engine_m5.py
from dataclasses import dataclass, field

from rs_spy.algo import risk
from rs_spy.backtest import broker_sim
from rs_spy.bias.buckets import BEAR, BULL, LONG_TRIGGER, NEUTRAL, SHORT_TRIGGER, STRONG_BEAR, STRONG_BULL
from rs_spy.bias.regime import CHOP, TREND_UP
from rs_spy.selection import watchlist

LONG = "LONG"
SHORT = "SHORT"

NEW_ENTRY_CUTOFF = pd.Timedelta(hours=15, minutes=30)
TIME_FLAT = pd.Timedelta(hours=15, minutes=55)
FINAL_STRETCH_START = pd.Timedelta(hours=15, minutes=30)
FINAL_STRETCH_TARGET_MULT = 0.75


def _et_time_of_day(index: pd.DatetimeIndex) -> pd.Series:
    et = index.tz_convert("America/New_York")
    return pd.Series(et - et.normalize(), index=index)


@dataclass
class PositionM5:
    symbol: str
    direction: str
    entry_bar: int
    entry_time: pd.Timestamp
    entry_price: float
    shares: float
    stop: float
    entry_atr: float
    peak_favorable: float = 0.0


@dataclass
class TradeM5:
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    shares: float
    exit_reason: str
    pnl: float
    r_multiple: float


@dataclass
class BacktestResultM5:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series | None = None

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([vars(t) for t in self.trades])


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
) -> BacktestResultM5:
    config = config or BacktestConfigM5()
    prepared = _prepare_m5(
        universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
        sectors, earnings_blackout, config,
    )
    calendar = prepared.calendar
    et_tod = _et_time_of_day(calendar)
    sessions = calendar.normalize()
    weeks = calendar.isocalendar().week.to_numpy()

    bias_ok_long_family = prepared.bias_df["bias"].isin([BULL, STRONG_BULL])
    bias_ok_long = bias_ok_long_family & bias_ok_long_family.shift(1, fill_value=False)
    bias_ok_short_family = prepared.bias_df["bias"].isin([BEAR, STRONG_BEAR])
    bias_ok_short = (
        bias_ok_short_family
        & bias_ok_short_family.shift(1, fill_value=False)
        & (prepared.regime_d1_m5 != TREND_UP)
    )
    in_entry_window = (~prepared.bias_df["warmup"]) & (et_tod <= NEW_ENTRY_CUTOFF)

    state_long = dict.fromkeys(universe_m5, watchlist.IDLE)
    state_short = dict.fromkeys(universe_m5, watchlist.IDLE)
    entry_path_long: dict = {}
    entry_path_short: dict = {}
    positions: dict = {}
    pending: dict = {}  # symbol -> broker_sim pending-entry dict
    entries_today_long: dict = {}
    entries_today_short: dict = {}
    locked_out_long: set = set()
    locked_out_short: set = set()

    risk_mgr = risk.RiskManager(starting_equity=config.starting_equity)
    equity = config.starting_equity
    equity_curve = []
    trades: list[TradeM5] = []

    prev_session = None
    prev_week = None

    for i, ts in enumerate(calendar):
        session = sessions[i]
        week = weeks[i]
        if session != prev_session:
            entries_today_long = {}
            entries_today_short = {}
            locked_out_long = set()
            locked_out_short = set()
            risk_mgr.new_session(equity)
            prev_session = session
        if week != prev_week:
            risk_mgr.new_week(equity)
            prev_week = week

        bias_now = prepared.bias_df["bias"].iat[i]
        flip_now = prepared.bias_df["flip_flatten"].iat[i]
        regime_now = prepared.regime_d1_m5.iat[i]
        time_now = et_tod.iat[i]

        # 1. try to fill pending entries (bar AFTER the signal bar)
        for sym, order in list(pending.items()):
            bar = prepared.bars[sym].iloc[i]
            if pd.isna(bar["open"]):
                order["bars_waited"] += 1
            else:
                fill = broker_sim.try_fill_entry(order["direction"], order["limit_price"], bar["open"], bar["high"], bar["low"])
                if fill is not None:
                    fill = broker_sim.apply_slippage(fill, order["direction"], is_entry=True)
                    positions[sym] = PositionM5(
                        symbol=sym, direction=order["direction"], entry_bar=i, entry_time=ts,
                        entry_price=fill, shares=order["shares"], stop=order["stop"], entry_atr=order["atr"],
                    )
                    book = entries_today_long if order["direction"] == LONG else entries_today_short
                    book[sym] = book.get(sym, 0) + 1
                    del pending[sym]
                    continue
                order["bars_waited"] += 1
            if order["bars_waited"] >= config.unfilled_cancel_bars:
                del pending[sym]

        # 2. manage open positions
        to_close = []
        for sym, pos in positions.items():
            bar = prepared.bars[sym].iloc[i]
            if pd.isna(bar["close"]):
                continue  # no fresh bar for this symbol -- carry the position forward unmanaged this bar
            atr = prepared.atr_m5[sym].iat[i]

            if pos.direction == LONG:
                if bar["low"] <= pos.stop:
                    to_close.append((sym, min(pos.stop, bar["open"]), "hard_stop"))
                    continue
                if bool(flip_now) and bias_now in (BEAR, STRONG_BEAR):
                    to_close.append((sym, bar["close"], "market_flip"))
                    continue
                if prepared.rs_failure_long[sym].iat[i]:
                    to_close.append((sym, bar["close"], "rs_failure"))
                    continue
                if prepared.vwap_loss_long[sym].iat[i]:
                    to_close.append((sym, bar["close"], "vwap_loss"))
                    continue
                favorable = bar["close"] - pos.entry_price
                pos.peak_favorable = max(pos.peak_favorable, favorable)
                target_mult = long_algo.PROFIT_TARGET_ATR_MULT
                if regime_now == CHOP:
                    target_mult *= long_algo.CHOP_PROFIT_TARGET_MULT
                if time_now >= FINAL_STRETCH_START:
                    target_mult *= FINAL_STRETCH_TARGET_MULT
                if prepared.momentum_stall_long[sym].iat[i] and favorable >= target_mult * pos.entry_atr:
                    to_close.append((sym, bar["close"], "profit_take"))
                    continue
                if bias_now == NEUTRAL and not pd.isna(atr):
                    pos.stop = risk.neutral_tighten_stop_long(pos.entry_price, atr, pos.stop, bar["close"])
                if pos.peak_favorable >= long_algo.TRAIL_TRIGGER_ATR_MULT * pos.entry_atr and not pd.isna(atr):
                    e8 = prepared.ema8[sym].iat[i]
                    trail = e8 - long_algo.TRAIL_STOP_ATR_MULT * atr
                    pos.stop = max(pos.stop, min(trail, pos.entry_price))
                if time_now >= TIME_FLAT:
                    to_close.append((sym, bar["close"], "time_flat"))
                    continue
            else:  # SHORT
                if bar["high"] >= pos.stop:
                    to_close.append((sym, max(pos.stop, bar["open"]), "hard_stop"))
                    continue
                if prepared.squeeze_guard_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "squeeze_guard"))
                    continue
                if bias_now in (BULL, STRONG_BULL):
                    to_close.append((sym, bar["close"], "market_flip"))
                    continue
                if prepared.rs_failure_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "rs_failure"))
                    continue
                if prepared.vwap_loss_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "vwap_loss"))
                    continue
                favorable = pos.entry_price - bar["close"]
                pos.peak_favorable = max(pos.peak_favorable, favorable)
                target_mult = short_algo.PROFIT_TARGET_ATR_MULT
                if regime_now == CHOP:
                    target_mult *= short_algo.CHOP_PROFIT_TARGET_MULT
                if time_now >= FINAL_STRETCH_START:
                    target_mult *= FINAL_STRETCH_TARGET_MULT
                if prepared.momentum_stall_short[sym].iat[i] and favorable >= target_mult * pos.entry_atr:
                    to_close.append((sym, bar["close"], "profit_take"))
                    continue
                if bias_now == NEUTRAL and not pd.isna(atr):
                    pos.stop = risk.neutral_tighten_stop_short(pos.entry_price, atr, pos.stop, bar["close"])
                if pos.peak_favorable >= short_algo.TRAIL_TRIGGER_ATR_MULT * pos.entry_atr and not pd.isna(atr):
                    e8 = prepared.ema8[sym].iat[i]
                    trail = e8 + short_algo.TRAIL_STOP_ATR_MULT * atr
                    pos.stop = min(pos.stop, max(trail, pos.entry_price))
                if time_now >= TIME_FLAT:
                    to_close.append((sym, bar["close"], "time_flat"))
                    continue

        for sym, exit_price, reason in to_close:
            pos = positions.pop(sym)
            exit_price = broker_sim.apply_slippage(exit_price, pos.direction, is_entry=False)
            pnl_per_share = (exit_price - pos.entry_price) if pos.direction == LONG else (pos.entry_price - exit_price)
            pnl = pnl_per_share * pos.shares
            stop_dist = abs(pos.entry_price - pos.stop) or pos.entry_atr or 1.0
            r_multiple = pnl_per_share / stop_dist
            equity += pnl
            trades.append(
                TradeM5(
                    symbol=sym, direction=pos.direction, entry_time=pos.entry_time, entry_price=pos.entry_price,
                    exit_time=ts, exit_price=exit_price, shares=pos.shares, exit_reason=reason, pnl=pnl,
                    r_multiple=r_multiple,
                )
            )
            if reason == "hard_stop":
                (locked_out_long if pos.direction == LONG else locked_out_short).add(sym)
            risk_mgr.register_exit(pnl, equity, reason, i)

        equity_curve.append(equity)

        # 3. update watchlist state (long book)
        can_enter_now = risk_mgr.can_enter(i) and in_entry_window.iat[i]
        for sym in universe_m5:
            gl = bool(prepared.gate_long[sym].iat[i])
            score = prepared.score_long[sym].iat[i]
            rrs_now = prepared.features[sym]["rolling_rrs_m5"].iat[i]
            rrs_prev = prepared.features[sym]["rolling_rrs_m5"].iat[i - 1] if i > 0 else None
            lrsi_now = prepared.features[sym]["lrsi_m5"].iat[i]
            lrsi_prev = prepared.features[sym]["lrsi_m5"].iat[i - 1] if i > 0 else None
            prev_state = state_long[sym]
            state_long[sym] = watchlist.next_state_long(
                prev_state, gl, score, rrs_prev, rrs_now,
                lrsi_prev=lrsi_prev, lrsi_now=lrsi_now,
                min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
            )
            if prev_state == watchlist.QUALIFIED and state_long[sym] == watchlist.DIP_ARMED:
                entry_path_long[sym] = "B"
            elif prev_state == watchlist.DIP_ARMED and state_long[sym] == watchlist.ENTRY_EVAL:
                pass  # entry_path_long[sym] already "B" from the prior bar
            if config.shorts_enabled:
                gs = bool(prepared.gate_short[sym].iat[i])
                score_s = prepared.score_short[sym].iat[i]
                prev_state_s = state_short[sym]
                state_short[sym] = watchlist.next_state_short(
                    prev_state_s, gs, score_s, rrs_prev, rrs_now,
                    lrsi_prev=lrsi_prev, lrsi_now=lrsi_now,
                    min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
                )
                if prev_state_s == watchlist.QUALIFIED and state_short[sym] == watchlist.DIP_ARMED:
                    entry_path_short[sym] = "B"

        trigger_now = prepared.bias_df["trigger"].iat[i]
        if bias_ok_long.iat[i] and trigger_now == LONG_TRIGGER:
            for sym in universe_m5:
                gl = bool(prepared.gate_long[sym].iat[i])
                if state_long[sym] == watchlist.QUALIFIED:
                    new_state = watchlist.apply_trigger_bypass(state_long[sym], gl, True)
                    if new_state != state_long[sym]:
                        state_long[sym] = new_state
                        entry_path_long[sym] = "A"
        if config.shorts_enabled and bias_ok_short.iat[i] and trigger_now == SHORT_TRIGGER:
            for sym in universe_m5:
                gs = bool(prepared.gate_short[sym].iat[i])
                if state_short[sym] == watchlist.QUALIFIED:
                    new_state = watchlist.apply_trigger_bypass(state_short[sym], gs, True)
                    if new_state != state_short[sym]:
                        state_short[sym] = new_state
                        entry_path_short[sym] = "A"

        # 4. submit entries for symbols now in ENTRY_EVAL
        if can_enter_now and bias_ok_long.iat[i]:
            eligible = {}
            for sym in universe_m5:
                if state_long[sym] != watchlist.ENTRY_EVAL or sym in positions or sym in pending:
                    continue
                if sym in locked_out_long or entries_today_long.get(sym, 0) >= config.max_entries_per_symbol_long:
                    continue
                path = entry_path_long.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_long[sym].iat[i] if path == "A" else prepared.dip_quality_long[sym].iat[i]
                )
                if qualifies:
                    eligible[sym] = prepared.score_long[sym].iat[i]
            tradeable = watchlist.build_tradeable_list(
                eligible, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            slots_free = config.max_concurrent_long - len(positions) - sum(1 for o in pending.values() if o["direction"] == LONG)
            for sym in tradeable[:slots_free]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    continue
                stop = risk.stop_price_long(bar["close"], atr)
                stop_dist = bar["close"] - stop
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_long[sym].iat[i], LONG,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, LONG)
                pending[sym] = {"direction": LONG, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}

        if config.shorts_enabled and can_enter_now and bias_ok_short.iat[i]:
            eligible_s = {}
            for sym in universe_m5:
                if state_short[sym] != watchlist.ENTRY_EVAL or sym in positions or sym in pending:
                    continue
                if sym in locked_out_short or entries_today_short.get(sym, 0) >= config.max_entries_per_symbol_short:
                    continue
                path = entry_path_short.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_short[sym].iat[i] if path == "A" else prepared.bounce_quality_short[sym].iat[i]
                )
                if qualifies:
                    eligible_s[sym] = prepared.score_short[sym].iat[i]
            tradeable_s = watchlist.build_tradeable_list(
                eligible_s, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            slots_free_s = config.max_concurrent_short - len(positions) - sum(1 for o in pending.values() if o["direction"] == SHORT)
            for sym in tradeable_s[:slots_free_s]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    continue
                stop = risk.stop_price_short(bar["close"], atr)
                stop_dist = stop - bar["close"]
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_short[sym].iat[i], SHORT,
                    short_size_multiplier=config.short_size_multiplier,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, SHORT)
                pending[sym] = {"direction": SHORT, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}

    equity_series = pd.Series(equity_curve, index=calendar)
    return BacktestResultM5(trades=trades, equity_curve=equity_series)
```

**Note for the implementer:** `long as long_algo` and `short as short_algo` are
already imported once at the top of the file in Task 5's half (see the imports
list at the start of that task's code block) — Task 6's code above relies on
those same two imports; do not re-import them here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_engine_m5_backtest.py -v`
Expected: PASS (8 tests total: 4 from Task 5 + 4 from this task)

- [ ] **Step 5: Run the full existing suite**

Run: `pytest -q`
Expected: all tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/rs_spy/backtest/engine_m5.py tests/unit/test_engine_m5_backtest.py
git commit -m "M6 Task 6: M5 event-driven backtest loop (backtest/engine_m5.py, run_m5_backtest)"
```

---

## Task 7: `scripts/run_backtest_intraday.py` — CLI wiring

**Files:**
- Create: `scripts/run_backtest_intraday.py`
- Test: `tests/integration/test_run_backtest_intraday_script.py`

**Interfaces:**
- Consumes: `rs_spy.backtest.engine_m5.{BacktestConfigM5, run_m5_backtest}`,
  `rs_spy.backtest.metrics.{compute_metrics, metrics_by_direction}`,
  `rs_spy.config.get_settings`, `rs_spy.data.loader.{load_universe_m1_bars,
  load_universe_m5_bars, load_universe_daily_bars}` (note: `load_universe_m1_bars`
  does not exist yet in `data/loader.py` — see Step 3, this task adds it as a thin
  wrapper mirroring the existing `load_universe_minute_bars`/`load_universe_m5_bars`
  pair, since `run_m5_backtest` needs the raw 1-minute bars too, not just M5),
  `rs_spy.data.warehouse.connect`, `rs_spy.universe.{load_earnings_blackout,
  load_universe}`.
- Produces: a runnable script writing `reports/m5_backtest/trades.csv` and
  `reports/m5_backtest/equity_curve.csv`, mirroring
  `scripts/run_backtest_d1.py`'s existing shape exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_run_backtest_intraday_script.py
"""Confirms the script's helper wiring works end-to-end against a small
synthetic universe -- this is NOT a network test (no Alpaca calls); it builds an
in-memory DuckDB warehouse directly, same pattern as
tests/integration/test_cache_resume.py."""
import duckdb
import numpy as np
import pandas as pd
import pytest


def _write_minute_bars(con, symbol: str, dates: list[str], seed: int) -> None:
    rng = np.random.default_rng(seed)
    rows = []
    for d in dates:
        idx = pd.date_range(f"{d} 09:30", periods=390, freq="1min", tz="America/New_York").tz_convert("UTC")
        close = 100.0 + np.cumsum(rng.normal(0, 0.05, 390))
        for ts, c in zip(idx, close):
            rows.append((symbol, "minute", ts.to_pydatetime(), c - 0.02, c + 0.05, c - 0.05, c, 1000.0))
    con.executemany(
        "INSERT INTO bars (symbol, timespan, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


@pytest.fixture
def warehouse(tmp_path):
    from rs_spy.data.warehouse import connect

    con = connect(str(tmp_path / "test.duckdb"))
    dates = [f"2026-02-{2 + i:02d}" for i in range(10)]
    _write_minute_bars(con, "SPY", dates, seed=1)
    _write_minute_bars(con, "QQQ", dates, seed=2)
    _write_minute_bars(con, "AAPL", dates, seed=3)
    yield con
    con.close()


def test_load_universe_m1_bars_exists_and_returns_a_dict_of_dataframes(warehouse):
    from rs_spy.data.loader import load_universe_m1_bars

    result = load_universe_m1_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    assert set(result.keys()) == {"SPY", "QQQ", "AAPL"}
    assert not result["SPY"].empty


def test_run_backtest_intraday_script_main_runs_end_to_end(warehouse, tmp_path, monkeypatch):
    from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
    from rs_spy.data.loader import (
        load_universe_daily_bars,
        load_universe_m1_bars,
        load_universe_m5_bars,
    )

    m1 = load_universe_m1_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    m5 = load_universe_m5_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    daily = {
        sym: df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        for sym, df in m1.items()
    }
    for sym in daily:
        daily[sym].index = daily[sym].index.tz_localize(None)

    result = run_m5_backtest(
        universe_m1={"AAPL": m1["AAPL"]},
        universe_m5={"AAPL": m5["AAPL"]},
        universe_d1={"AAPL": daily["AAPL"]},
        spy_m1=m1["SPY"], spy_m5=m5["SPY"], spy_d1=daily["SPY"],
        qqq_m1=m1["QQQ"], qqq_m5=m5["QQQ"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert result.equity_curve is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_run_backtest_intraday_script.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_universe_m1_bars'`

- [ ] **Step 3: Add `load_universe_m1_bars` to `data/loader.py`**

Append to `src/rs_spy/data/loader.py` (after `load_universe_minute_bars`, around
line 45):

```python
def load_universe_m1_bars(
    con: duckdb.DuckDBPyConnection, symbols: list[str], rth_only: bool = True
) -> dict[str, pd.DataFrame]:
    """Alias of load_universe_minute_bars -- named to match load_universe_m5_bars'
    "m1"/"m5" naming for callers (backtest/engine_m5.py) that need both cadences
    side by side and read more clearly with parallel names."""
    return load_universe_minute_bars(con, symbols, rth_only=rth_only)
```

- [ ] **Step 4: Run tests to verify the loader test passes**

Run: `pytest tests/integration/test_run_backtest_intraday_script.py::test_load_universe_m1_bars_exists_and_returns_a_dict_of_dataframes -v`
Expected: PASS

- [ ] **Step 5: Write `scripts/run_backtest_intraday.py`**

```python
# scripts/run_backtest_intraday.py
"""M6: run the M5 event-driven backtest over the full cached minute-bar history
for the curated universe, print 08 §2 metrics, and write the trade log to
reports/m5_backtest/trades.csv."""
import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics, metrics_by_direction
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(shorts: bool = False) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    all_m1 = load_universe_m1_bars(con, universe.all_symbols)
    all_m5 = load_universe_m5_bars(con, universe.all_symbols)
    all_d1 = load_universe_daily_bars(con, universe.all_symbols)

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    sectors = {s.symbol: s.sector for s in universe.universe}

    config = BacktestConfigM5(shorts_enabled=shorts)
    typer.echo(f"Running M5 backtest: {len(trade_symbols)} symbols, shorts_enabled={shorts}")
    result = run_m5_backtest(
        universe_m1={s: all_m1[s] for s in trade_symbols},
        universe_m5={s: all_m5[s] for s in trade_symbols},
        universe_d1={s: all_d1[s] for s in trade_symbols},
        spy_m1=all_m1[spy], spy_m5=all_m5[spy], spy_d1=all_d1[spy],
        qqq_m1=all_m1[qqq], qqq_m5=all_m5[qqq],
        sectors=sectors,
        earnings_blackout=earnings_blackout,
        config=config,
    )

    trades = result.trades_df()
    trading_days = len(result.equity_curve.index.normalize().unique()) if result.equity_curve is not None else 0
    metrics = compute_metrics(trades, result.equity_curve, trading_days)

    typer.echo(f"\n{len(trades)} trades over {trading_days} trading days")
    for k, v in metrics.items():
        typer.echo(f"  {k}: {v}")

    if not trades.empty:
        typer.echo("\nBy direction:")
        for direction, m in metrics_by_direction(trades, config.starting_equity).items():
            typer.echo(f"  {direction}: {m}")
        typer.echo("\nExit reason breakdown:")
        typer.echo(trades["exit_reason"].value_counts().to_string())

    out_dir = settings.reports_dir / "m5_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False)
    if result.equity_curve is not None:
        result.equity_curve.to_csv(out_dir / "equity_curve.csv")
    typer.echo(f"\nWrote trade log to {out_dir / 'trades.csv'}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Run all new tests**

Run: `pytest tests/integration/test_run_backtest_intraday_script.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Run the full existing suite**

Run: `pytest -q`
Expected: all tests pass, no regressions. Also run `ruff check .` and confirm clean.

- [ ] **Step 8: Commit**

```bash
git add scripts/run_backtest_intraday.py src/rs_spy/data/loader.py tests/integration/test_run_backtest_intraday_script.py
git commit -m "M6 Task 7: intraday backtest CLI script (scripts/run_backtest_intraday.py)"
```

---

## Task 8: Document M6 in IMPLEMENTATION.md

**Files:**
- Modify: `IMPLEMENTATION.md`

**Interfaces:**
- Consumes: the actual `git log`/`git diff` from Tasks 1-7 plus the real output of
  running `python scripts/run_backtest_intraday.py` against the real cached
  warehouse (not the synthetic test fixtures) — this task's job is to report real
  results, not to write speculative prose.

- [ ] **Step 1: Run the real backtest and capture output**

```bash
python scripts/run_backtest_intraday.py
```

Record: trade count, win rate, profit factor, exit-reason breakdown, and any
crash/exception (if it crashes against the real 5-year/130-symbol warehouse due to
runtime, memory, or a data-shape issue not caught by the synthetic test fixtures,
record exactly what failed — this is expected to surface issues the small
synthetic fixtures in Tasks 5-7 could not, the same way M5's real-data run
surfaced the `gap_pct`/H1-resample bugs that unit tests alone didn't catch. Fix
straightforward bugs directly; if a fix is non-trivial, stop and report back
instead of guessing).

- [ ] **Step 2: Update the milestone tracker**

In `IMPLEMENTATION.md`, change:

```markdown
- M6: M5-cadence event-driven backtest engine, long/short algo per
  algo-spec 05/06/07 — **not started**.
```

to:

```markdown
- **M6: M5-cadence event-driven backtest engine, long/short algo per
  algo-spec 05/06/07 — complete (this checkpoint).** See "M6:..." section below.
```

- [ ] **Step 3: Add a new "M6: ..." section**

Insert a new section after the existing "M5: full intraday market-bias +
stock-selection engines" section (before "## Known limitations / open risks"),
following the exact structure of the M5 section: what was built (list every new
file from Tasks 1-7 with a one-line description), real bugs found and fixed during
the build (list whatever Tasks 1-7's implementers and reviewers actually found —
do not invent bugs that weren't found; if none were found beyond what this plan's
own Task 5 implementer-note already anticipated, say so plainly), disclosed
simplifications (07 §6 kill switches, stop-distance ATR-only simplification, the
dip/bounce-quality translation — all already called out in this plan's Global
Constraints and Tasks 1/3/4's docstrings; pull the exact wording from there rather
than re-deriving it), and the real backtest run's results from Step 1.

- [ ] **Step 4: Update "Known limitations / open risks"**

Add new numbered items for anything genuinely still open after M6 — at minimum:
the 07 §6 kill switches (not implemented, live-only), the ATR-only stop
simplification (07 §3's swing-low alternative dropped), the dip/bounce-quality
proxy translation (05 §3/06 §3), and whatever the real backtest run in Step 1
surfaces as a live concern for M7 (e.g., if trade count is very low again — as it
was for the D1 skeleton at 28 symbols — note it plainly rather than treating a
thin M6 sample as a validated result, matching this project's own established
practice of flagging exactly that kind of gap during M3.5 and M5's final review).

- [ ] **Step 5: Update the "Next" section**

Replace the "## Next: M6 ..." section with a "## Next: M7 (full validation study
suite + reporting)" section pointing at algo-spec 08 §3's five required studies
(rule-count ablation, walk-away analysis, RRS parameter sensitivity, bias-engine
confusion matrix, time-of-day/regime slicing) at M5 cadence, extending the
existing `backtest/studies/` modules the same way M3.5 built them for D1.

- [ ] **Step 6: Commit**

```bash
git add IMPLEMENTATION.md
git commit -m "M6 Task 8: document M6 milestone in IMPLEMENTATION.md"
```

---

## Self-review notes (from the plan author, not a task to execute)

- **Spec coverage:** 05 §1 (preconditions) → Task 6's `bias_ok_long`/`in_entry_window`
  checks; 05 §2 (path A) → Task 3's `confirm_trigger_entry_long` + Task 6's
  `apply_trigger_bypass` wiring; 05 §3 (path B) → Task 3's `dip_quality_pass_long` +
  Task 6's DIP_ARMED→ENTRY_EVAL wiring; 05 §4 (position management, all 7 rules) →
  Tasks 3/6; 05 §5 (re-entry limits) → Task 6's `entries_today_long`/
  `locked_out_long`. 06 mirrors via Tasks 4/6, plus the two documented asymmetries
  (unconditional bull-flip exit, squeeze guard). 07 §1 (account params) → Task 1's
  constants; §2 (sizing) → `position_size`/`cap_shares`; §3 (stops) →
  `stop_price_long/short` (simplified, disclosed); §4 (dynamic tightening) →
  `neutral_tighten_stop_*` (Task 1) + final-stretch target reduction (Task 6); §5
  (order handling) → Task 2; §6 (kill switches) → explicitly not built, disclosed
  in Global Constraints and Task 8. 08 §1 (fills/timing) → Task 2's next-bar-fill
  convention. 08 §2-5 (metrics/studies) are M7, not this plan — `backtest/metrics.py`
  is reused as-is (already cadence-agnostic) in Task 7.
- **Placeholder scan:** every step above has real code, real assertions, and real
  expected outputs; no "TBD"/"add error handling"/"similar to Task N" language.
- **Type consistency check:** `BacktestConfigM5`, `PreparedM5`, `PositionM5`,
  `TradeM5`, `BacktestResultM5` are defined once (Tasks 5/6) and referenced by
  those exact names in Task 7's script. `algo.long`/`algo.short`'s function names
  (Tasks 3/4) match exactly what Task 5's `_prepare_m5` imports and calls. `risk.py`
  and `broker_sim.py`'s function signatures (Tasks 1/2) match exactly what Task 6's
  event loop calls them with.
- **Known residual risk, disclosed rather than hidden:** Task 6's event loop is the
  largest, most integration-heavy piece of code in this plan, hand-written without
  the ability to execute it before handoff (unlike the smaller, independently
  testable Tasks 1-4). Its implementer and reviewer should expect to find and fix
  real bugs — this is normal and expected, the same way M5's own plan needed
  fixes during Tasks 4/5/7/8 (see IMPLEMENTATION.md's M5 section for that
  precedent). Budget for at least one fix-and-re-review round on Task 6 before
  moving on.
