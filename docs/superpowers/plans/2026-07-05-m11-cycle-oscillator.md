# M11 Phase 1: Cycle Oscillator Skill Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the spec (`docs/superpowers/specs/2026-07-05-cycle-oscillator-design.md`): parameterized 1OP-style two-line cycle oscillators on SPY M5 (close-PPO and VWAP-deviation families), a skill study that scores each candidate's 4-state read against forward SPY returns on a 2021–2024 train window, an incumbent-bias-engine baseline scored identically, a trigger-composition read, and a single-shot pre-committed holdout gate on 2025–2026 — **no engine wiring in this milestone**.

**Architecture:** One pure indicator module (`indicators/cycle_oscillator.py`: OscSpec → lines → states/crosses), one pure study module (`backtest/studies/oscillator_skill_m5.py`: window split, state/cross skill tables, separation scores, incumbent scorer, trigger composition, winner selection, holdout verdict), one typer driver (`scripts/run_oscillator_study.py`, `train` and `holdout` subcommands; holdout accepts exactly one spec). Everything experiment-friendly: small composable functions, dataclass specs, DataFrames in/out.

**Tech Stack:** Python 3.11+, pandas, DuckDB (read-only warehouse loads in the driver only), typer, pytest.

**Working copy:** the `m11-cycle-oscillator` worktree at `/Users/johnoverton/Development/rs-spy/.worktrees/m11-cycle-oscillator` (branch `m11-cycle-oscillator`; its own `.venv`; `data/` is a symlink to the main repo's warehouse). All commits go to this branch, never main.

## Global Constraints

- **No engine wiring**: `engine_m5.py`, `bias/engine.py`, `BacktestConfigM5`, and all default behavior untouched. This milestone only ADDS indicator/study/driver/test files.
- **No lookahead**: all EMAs `ewm(span=..., adjust=False)` (causal); forward returns via `close.shift(-h)`; the oscillator at bar t uses bars ≤ t only.
- **Train/holdout split enforced in code**: `TRAIN_END = pd.Timestamp("2025-01-01")` (train = index < TRAIN_END, holdout = index ≥ TRAIN_END). Winner selection functions accept train frames only by construction; the holdout driver refuses more than one spec.
- **Pre-committed selection metric**: `sep_h = mean_fwd_h(BULL_RUN ∪ BULL_EARLY) − mean_fwd_h(BEAR_RUN ∪ BEAR_EARLY)`; primary `sep_24`, tie-break `sep_12`; train eligibility = every state n ≥ 200. **Pre-committed holdout gate** (each state n ≥ 50): `sep_24 > 0` AND `sep_12 > 0` AND `sep_24(winner) > sep_24(incumbent on holdout)` AND `sign(sep_24)` matches train.
- **Candidate grid exactly**: `input_mode ∈ {close, vwap_dev}` × `(fast, slow) ∈ {(6,13),(9,21),(12,26),(16,36)}` × `signal ∈ {5,9,13}` = 24 candidates.
- **State vocabulary exactly**: `BULL_RUN` (fast>signal, fast>0), `BULL_EARLY` (fast>signal, fast≤0), `BEAR_EARLY` (fast≤signal, fast>0), `BEAR_RUN` (fast≤signal, fast≤0).
- Incumbent bucket→side mapping: bull side = {STRONG_BULL, BULL}, bear side = {STRONG_BEAR, BEAR}, NEUTRAL excluded (constants from `rs_spy.bias.buckets`).
- All unit tests hermetic (synthetic frames; no warehouse/network). `ruff check .` (line-length 100) clean before every commit. Run from the worktree root with `source .venv/bin/activate`.
- "Document, don't silently approximate": cross-session EMA carryover, session-crossing forward returns, and the vwap_dev family's unused `slow` parameter each get a docstring sentence.

## File structure

```
src/rs_spy/indicators/cycle_oscillator.py      CREATE  OscSpec, compute_oscillator, oscillator_states, oscillator_crosses
src/rs_spy/backtest/studies/oscillator_skill_m5.py  CREATE  split_train_holdout, state_skill_table,
                                                     separation_scores, cross_skill_table, incumbent_skill,
                                                     trigger_composition_table, candidate_grid, run_train_sweep,
                                                     holdout_verdict
scripts/run_oscillator_study.py                CREATE  typer: train / holdout subcommands
tests/unit/test_cycle_oscillator.py            CREATE
tests/unit/test_oscillator_skill.py            CREATE
```

---

### Task 1: The indicator — `indicators/cycle_oscillator.py`

**Files:**
- Create: `src/rs_spy/indicators/cycle_oscillator.py`
- Test: `tests/unit/test_cycle_oscillator.py`

**Interfaces:**
- Consumes: `rs_spy.indicators.vwap.vwap(df) -> pd.Series` (session VWAP; caller pre-filters RTH — the standard M5 loader frames already are).
- Produces (Tasks 2-4 rely on these EXACTLY): frozen `OscSpec(input_mode: str, fast: int, slow: int, signal: int)` with a `name` property returning `f"{input_mode}-{fast}-{slow}-{signal}"`; `compute_oscillator(m5: pd.DataFrame, spec: OscSpec) -> pd.DataFrame` with columns `fast_line, signal_line, histogram` (float, same index as `m5`); `oscillator_states(osc: pd.DataFrame) -> pd.Series` (object dtype, values from `STATES = ("BULL_RUN", "BULL_EARLY", "BEAR_EARLY", "BEAR_RUN")`, NaN where lines are NaN); `oscillator_crosses(osc: pd.DataFrame) -> pd.DataFrame` with boolean columns `bull_cross, bear_cross, zero_up, zero_down`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cycle_oscillator.py`:

```python
"""Cycle oscillator: PPO/vwap_dev lines, 4-state read, cross events (hermetic)."""
import numpy as np
import pandas as pd
import pytest

from rs_spy.indicators.cycle_oscillator import (
    STATES,
    OscSpec,
    compute_oscillator,
    oscillator_crosses,
    oscillator_states,
)


def _m5(closes, volumes=None):
    idx = pd.date_range("2026-03-02 14:30", periods=len(closes), freq="5min", tz="UTC")
    closes = pd.Series(closes, index=idx, dtype=float)
    vol = pd.Series(volumes if volumes is not None else 1_000.0, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes + 0.1, "low": closes - 0.1,
         "close": closes, "volume": vol}
    )


def test_spec_name_is_derived():
    assert OscSpec("close", 12, 26, 9).name == "close-12-26-9"


def test_close_mode_matches_hand_computed_ppo():
    m5 = _m5([100.0, 101.0, 102.0, 101.0, 100.0, 99.0])
    spec = OscSpec("close", 2, 4, 2)
    osc = compute_oscillator(m5, spec)
    ema_f = m5["close"].ewm(span=2, adjust=False).mean()
    ema_s = m5["close"].ewm(span=4, adjust=False).mean()
    expected_fast = 100.0 * (ema_f - ema_s) / ema_s
    pd.testing.assert_series_equal(osc["fast_line"], expected_fast, check_names=False)
    expected_signal = expected_fast.ewm(span=2, adjust=False).mean()
    pd.testing.assert_series_equal(osc["signal_line"], expected_signal, check_names=False)
    pd.testing.assert_series_equal(
        osc["histogram"], expected_fast - expected_signal, check_names=False
    )


def test_vwap_dev_mode_oscillates_around_session_vwap():
    # constant volume, price walking above the session mean -> positive dev
    m5 = _m5([100.0, 100.0, 100.0, 104.0, 104.0, 104.0])
    spec = OscSpec("vwap_dev", 2, 4, 2)
    osc = compute_oscillator(m5, spec)
    assert osc["fast_line"].iloc[-1] > 0          # trading above VWAP
    m5_down = _m5([100.0, 100.0, 100.0, 96.0, 96.0, 96.0])
    assert compute_oscillator(m5_down, spec)["fast_line"].iloc[-1] < 0


def test_oscillator_is_causal():
    closes = list(np.linspace(100, 110, 40))
    m5 = _m5(closes)
    spec = OscSpec("close", 6, 13, 5)
    full = compute_oscillator(m5, spec)
    truncated = compute_oscillator(m5.iloc[:30], spec)
    pd.testing.assert_frame_equal(full.iloc[:30], truncated)  # future bars change nothing


def test_states_cover_the_four_quadrants():
    idx = pd.date_range("2026-03-02 14:30", periods=4, freq="5min", tz="UTC")
    osc = pd.DataFrame(
        {
            "fast_line": [1.0, -0.5, 0.5, -1.0],
            "signal_line": [0.5, -1.0, 1.0, -0.5],
        },
        index=idx,
    )
    osc["histogram"] = osc["fast_line"] - osc["signal_line"]
    states = oscillator_states(osc)
    assert list(states) == ["BULL_RUN", "BULL_EARLY", "BEAR_EARLY", "BEAR_RUN"]
    assert set(STATES) == set(states)


def test_states_are_nan_where_lines_are_nan():
    idx = pd.date_range("2026-03-02 14:30", periods=2, freq="5min", tz="UTC")
    osc = pd.DataFrame(
        {"fast_line": [np.nan, 1.0], "signal_line": [np.nan, 0.5],
         "histogram": [np.nan, 0.5]},
        index=idx,
    )
    states = oscillator_states(osc)
    assert pd.isna(states.iloc[0]) and states.iloc[1] == "BULL_RUN"


def test_crosses_fire_only_on_the_crossing_bar():
    idx = pd.date_range("2026-03-02 14:30", periods=5, freq="5min", tz="UTC")
    osc = pd.DataFrame(
        {
            "fast_line": [-1.0, -0.2, 0.3, 0.6, 0.4],
            "signal_line": [-0.5, -0.4, 0.1, 0.4, 0.5],
        },
        index=idx,
    )
    osc["histogram"] = osc["fast_line"] - osc["signal_line"]
    crosses = oscillator_crosses(osc)
    assert list(crosses["bull_cross"]) == [False, True, False, False, False]
    assert list(crosses["bear_cross"]) == [False, False, False, False, True]
    assert list(crosses["zero_up"]) == [False, False, True, False, False]
    assert list(crosses["zero_down"]) == [False, False, False, False, False]


def test_unknown_input_mode_raises():
    with pytest.raises(ValueError, match="nope"):
        compute_oscillator(_m5([100.0, 101.0]), OscSpec("nope", 2, 4, 2))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cycle_oscillator.py -q`
Expected: FAIL with `ModuleNotFoundError: rs_spy.indicators.cycle_oscillator`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/indicators/cycle_oscillator.py`:

```python
"""1OP-style two-line cycle oscillator on M5 bars (M11 Phase 1).

Two input families (spec 2026-07-05-cycle-oscillator-design.md):
  * "close"    -- PPO: 100 * (EMA(close, fast) - EMA(close, slow)) / EMA(close, slow).
  * "vwap_dev" -- fast EMA of the percentage deviation from session VWAP
                  (price+volume composite; `slow` is unused by this formula and
                  kept on the spec only for uniform grid bookkeeping).
Both: signal_line = EMA(fast_line, signal); histogram = fast_line - signal_line.

Causal by construction (adjust=False EWMs, no shifts backward). The oscillator's
EMA state deliberately carries across sessions (only the VWAP input resets
daily) -- the conventional way MACD-family indicators are run intraday,
documented rather than silently chosen.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from rs_spy.indicators.vwap import vwap

STATES = ("BULL_RUN", "BULL_EARLY", "BEAR_EARLY", "BEAR_RUN")
INPUT_MODES = ("close", "vwap_dev")


@dataclass(frozen=True)
class OscSpec:
    input_mode: str
    fast: int
    slow: int
    signal: int

    @property
    def name(self) -> str:
        return f"{self.input_mode}-{self.fast}-{self.slow}-{self.signal}"


def compute_oscillator(m5: pd.DataFrame, spec: OscSpec) -> pd.DataFrame:
    """fast_line / signal_line / histogram on m5's index (RTH M5 bars)."""
    if spec.input_mode == "close":
        ema_fast = m5["close"].ewm(span=spec.fast, adjust=False).mean()
        ema_slow = m5["close"].ewm(span=spec.slow, adjust=False).mean()
        fast_line = 100.0 * (ema_fast - ema_slow) / ema_slow
    elif spec.input_mode == "vwap_dev":
        session_vwap = vwap(m5)
        dev = 100.0 * (m5["close"] - session_vwap) / session_vwap
        fast_line = dev.ewm(span=spec.fast, adjust=False).mean()
    else:
        raise ValueError(f"unknown input_mode: {spec.input_mode!r}")

    signal_line = fast_line.ewm(span=spec.signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "fast_line": fast_line,
            "signal_line": signal_line,
            "histogram": fast_line - signal_line,
        }
    )


def oscillator_states(osc: pd.DataFrame) -> pd.Series:
    """The 4-state read: (fast vs signal) x (fast vs zero). NaN-preserving."""
    fast, signal = osc["fast_line"], osc["signal_line"]
    above_signal = fast > signal
    above_zero = fast > 0
    states = np.select(
        [
            above_signal & above_zero,
            above_signal & ~above_zero,
            ~above_signal & above_zero,
        ],
        ["BULL_RUN", "BULL_EARLY", "BEAR_EARLY"],
        default="BEAR_RUN",
    )
    out = pd.Series(states, index=osc.index, dtype=object)
    out[fast.isna() | signal.isna()] = np.nan
    return out


def oscillator_crosses(osc: pd.DataFrame) -> pd.DataFrame:
    """True only on the crossing bar."""
    fast, signal = osc["fast_line"], osc["signal_line"]
    above = fast > signal
    above_prev = above.shift(1).fillna(False).astype(bool)
    pos = fast > 0
    pos_prev = pos.shift(1).fillna(False).astype(bool)
    return pd.DataFrame(
        {
            "bull_cross": above & ~above_prev,
            "bear_cross": ~above & above_prev,
            "zero_up": pos & ~pos_prev,
            "zero_down": ~pos & pos_prev,
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cycle_oscillator.py -q` — Expected: 8 passed.
Then: `python -m pytest -q -m "not integration"` and `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/indicators/cycle_oscillator.py tests/unit/test_cycle_oscillator.py
git commit -m "M11: cycle oscillator indicator (PPO + vwap_dev, 4 states, crosses)"
```

---

### Task 2: Study core — windows, state/cross skill, separation

**Files:**
- Create: `src/rs_spy/backtest/studies/oscillator_skill_m5.py`
- Test: `tests/unit/test_oscillator_skill.py`

**Interfaces:**
- Consumes: Task 1's `oscillator_states` output shape (object Series of STATES) — but functions here take generic Series (no oscillator import needed yet).
- Produces (Tasks 3-4 rely on these EXACTLY): `TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")`; `HORIZONS = (12, 24, 78)`; `BULL_STATES = ("BULL_RUN", "BULL_EARLY")`; `BEAR_STATES = ("BEAR_RUN", "BEAR_EARLY")`; `split_train_holdout(df: pd.DataFrame | pd.Series) -> tuple` (index < / ≥ TRAIN_END, raises `ValueError` if either side is empty); `state_skill_table(states: pd.Series, close: pd.Series, horizons=HORIZONS) -> pd.DataFrame` (one row per state × horizon: `state, horizon_bars, n, mean_fwd_return, median_fwd_return`; forward return = `close.shift(-h)/close - 1`, NaN-forward rows excluded from n — same conventions as `trigger_skill_m5.trigger_skill_table`); `separation_scores(table: pd.DataFrame) -> dict` (`{"sep_12": float|None, "sep_24": ..., "sep_78": ..., "min_state_n": int}` where `sep_h` = n-weighted mean of bull-state means minus n-weighted mean of bear-state means at horizon h; None when either side has n=0); `cross_skill_table(crosses: pd.DataFrame, close: pd.Series, horizons=HORIZONS) -> pd.DataFrame` (row per cross column × horizon, same stats).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_oscillator_skill.py`:

```python
"""Oscillator skill study core: windows, forward-return tables, separation (hermetic)."""
import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.studies.oscillator_skill_m5 import (
    BEAR_STATES,
    BULL_STATES,
    HORIZONS,
    TRAIN_END,
    cross_skill_table,
    separation_scores,
    split_train_holdout,
    state_skill_table,
)


def _series(values, start="2024-12-31 14:30", freq="5min"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx)


def test_split_train_holdout_boundary():
    s = _series(range(10), start="2024-12-31 21:40")  # crosses 2025-01-01 UTC
    train, holdout = split_train_holdout(s)
    assert (train.index < TRAIN_END).all() and (holdout.index >= TRAIN_END).all()
    assert len(train) + len(holdout) == 10 and len(train) > 0 and len(holdout) > 0


def test_split_refuses_empty_side():
    s = _series(range(5), start="2026-01-05 14:30")  # all holdout
    with pytest.raises(ValueError, match="empty"):
        split_train_holdout(s)


def test_state_skill_table_known_answer():
    # close doubles every bar in BULL, halves in BEAR -> unambiguous separation
    close = _series([100, 200, 400, 200, 100, 50], start="2024-01-02 14:30")
    states = pd.Series(
        ["BULL_RUN", "BULL_RUN", "BEAR_RUN", "BEAR_RUN", "BEAR_RUN", "BEAR_RUN"],
        index=close.index, dtype=object,
    )
    table = state_skill_table(states, close, horizons=(1,))
    bull = table[(table.state == "BULL_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bull["n"] == 2
    assert bull["mean_fwd_return"] == pytest.approx(1.0)   # +100% twice
    bear = table[(table.state == "BEAR_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bear["n"] == 3   # last bar has no forward return -> excluded
    assert bear["mean_fwd_return"] == pytest.approx(-0.5)


def test_state_skill_table_excludes_nan_states_and_nan_forwards():
    close = _series([100, 110, 121], start="2024-01-02 14:30")
    states = pd.Series([np.nan, "BULL_RUN", "BULL_RUN"], index=close.index, dtype=object)
    table = state_skill_table(states, close, horizons=(1,))
    bull = table[(table.state == "BULL_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bull["n"] == 1   # NaN state row and no-forward last row both excluded


def test_separation_scores_weighted_and_signed():
    rows = []
    for state, n, mean in (
        ("BULL_RUN", 3, 0.02), ("BULL_EARLY", 1, -0.02),   # weighted bull mean = 0.01
        ("BEAR_RUN", 2, -0.03), ("BEAR_EARLY", 2, 0.01),   # weighted bear mean = -0.01
    ):
        rows.append({"state": state, "horizon_bars": 24, "n": n,
                     "mean_fwd_return": mean, "median_fwd_return": mean})
    scores = separation_scores(pd.DataFrame(rows))
    assert scores["sep_24"] == pytest.approx(0.02)
    assert scores["sep_12"] is None            # horizon absent
    assert scores["min_state_n"] == 1


def test_separation_none_when_a_side_is_empty():
    rows = [{"state": "BULL_RUN", "horizon_bars": 24, "n": 5,
             "mean_fwd_return": 0.01, "median_fwd_return": 0.01}]
    assert separation_scores(pd.DataFrame(rows))["sep_24"] is None


def test_cross_skill_table_scores_each_cross_column():
    close = _series([100, 110, 121, 133.1], start="2024-01-02 14:30")
    crosses = pd.DataFrame(
        {"bull_cross": [False, True, False, False],
         "bear_cross": [False, False, False, False],
         "zero_up": [False, False, True, False],
         "zero_down": [False, False, False, False]},
        index=close.index,
    )
    table = cross_skill_table(crosses, close, horizons=(1,))
    bull = table[(table.event == "bull_cross") & (table.horizon_bars == 1)].iloc[0]
    assert bull["n"] == 1 and bull["mean_fwd_return"] == pytest.approx(0.10)
    bear = table[(table.event == "bear_cross") & (table.horizon_bars == 1)].iloc[0]
    assert bear["n"] == 0 and bear["mean_fwd_return"] is None


def test_default_constants_are_the_spec_values():
    assert HORIZONS == (12, 24, 78)
    assert BULL_STATES == ("BULL_RUN", "BULL_EARLY")
    assert BEAR_STATES == ("BEAR_RUN", "BEAR_EARLY")
    assert str(TRAIN_END.date()) == "2025-01-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/backtest/studies/oscillator_skill_m5.py`:

```python
"""M11 Phase 1: does a cycle oscillator's state read have forward-return skill?

Pure functions over supplied frames (hermetic tests use synthetic bars);
scripts/run_oscillator_study.py is the real-data shell. Forward-return
conventions match trigger_skill_m5: fwd = close.shift(-h)/close - 1, rows
without h bars of subsequent history are excluded from n (this lets horizons
cross session boundaries into the next session -- same disclosed convention
as the M7.5 trigger-skill study).

Train/holdout discipline (spec, pre-committed): TRAIN_END splits the windows;
selection happens on train only; the holdout driver evaluates exactly one
candidate, once.
"""
import pandas as pd

TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")
HORIZONS = (12, 24, 78)
BULL_STATES = ("BULL_RUN", "BULL_EARLY")
BEAR_STATES = ("BEAR_RUN", "BEAR_EARLY")


def split_train_holdout(obj):
    """(train, holdout) by TRAIN_END. Raises if either side is empty."""
    train = obj[obj.index < TRAIN_END]
    holdout = obj[obj.index >= TRAIN_END]
    if len(train) == 0 or len(holdout) == 0:
        raise ValueError(
            f"empty window: train={len(train)} holdout={len(holdout)} rows "
            f"(TRAIN_END={TRAIN_END.date()})"
        )
    return train, holdout


def _fwd_stats(fwd: pd.Series, mask: pd.Series) -> dict:
    sub = fwd[mask & fwd.notna()]
    n = len(sub)
    return {
        "n": n,
        "mean_fwd_return": float(sub.mean()) if n else None,
        "median_fwd_return": float(sub.median()) if n else None,
    }


def state_skill_table(
    states: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    """One row per (state, horizon): n / mean / median forward return."""
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for state in (*BULL_STATES, *BEAR_STATES):
            rows.append(
                {"state": state, "horizon_bars": horizon,
                 **_fwd_stats(fwd, states == state)}
            )
    return pd.DataFrame(rows, dtype=object)


def separation_scores(table: pd.DataFrame) -> dict:
    """sep_h = n-weighted bull-state mean minus n-weighted bear-state mean.

    None when a horizon is absent or either side has zero observations.
    min_state_n = smallest per-(state, horizon) n; floors bind against true occupancy (tightened in Task-2 review, c9ed9b5).
    """
    out: dict = {}
    horizons = sorted({int(h) for h in table["horizon_bars"]})
    for h in (12, 24, 78):
        if h not in horizons:
            out[f"sep_{h}"] = None
            continue
        sub = table[table["horizon_bars"] == h]

        def side_mean(names):
            side = sub[sub["state"].isin(names) & sub["mean_fwd_return"].notna()]
            n = side["n"].astype(float).sum()
            if n == 0:
                return None
            return float(
                (side["mean_fwd_return"].astype(float) * side["n"].astype(float)).sum() / n
            )

        bull, bear = side_mean(BULL_STATES), side_mean(BEAR_STATES)
        out[f"sep_{h}"] = None if bull is None or bear is None else bull - bear
    out["min_state_n"] = int(table["n"].astype(int).min()) if len(table) else 0  # per-(state,horizon) occupancy -- tightened in review (c9ed9b5), do not revert
    return out


def cross_skill_table(
    crosses: pd.DataFrame, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for event in crosses.columns:
            rows.append(
                {"event": event, "horizon_bars": horizon,
                 **_fwd_stats(fwd, crosses[event])}
            )
    return pd.DataFrame(rows, dtype=object)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q` — Expected: 8 passed.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/backtest/studies/oscillator_skill_m5.py tests/unit/test_oscillator_skill.py
git commit -m "M11: skill-study core (windows, state/cross tables, separation)"
```

---

### Task 3: Incumbent baseline + trigger composition

**Files:**
- Modify: `src/rs_spy/backtest/studies/oscillator_skill_m5.py` (append)
- Test: `tests/unit/test_oscillator_skill.py` (append)

**Interfaces:**
- Consumes: `rs_spy.bias.buckets` constants (`STRONG_BULL, BULL, BEAR, STRONG_BEAR, LONG_TRIGGER`); Task 2's `state_skill_table`/`separation_scores`.
- Produces (Task 4 relies on these EXACTLY): `INCUMBENT_BULL = (STRONG_BULL, BULL)`, `INCUMBENT_BEAR = (STRONG_BEAR, BEAR)`; `incumbent_skill(bias: pd.Series, close: pd.Series, horizons=HORIZONS) -> tuple[pd.DataFrame, dict]` — maps the bucket series onto the oscillator state vocabulary (STRONG_BULL/BULL→BULL_RUN, BEAR/STRONG_BEAR→BEAR_RUN, everything else NaN; BULL_EARLY/BEAR_EARLY stay empty) then reuses `state_skill_table` + `separation_scores`, so the incumbent is scored with the identical metric; `trigger_composition_table(trigger: pd.Series, states: pd.Series, close: pd.Series, horizons=HORIZONS) -> pd.DataFrame` — rows for LONG_TRIGGER events overall (`state="ALL"`) and per oscillator state at the trigger bar (`state, horizon_bars, n, mean_fwd_return, median_fwd_return`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_oscillator_skill.py`:

```python
from rs_spy.backtest.studies.oscillator_skill_m5 import (  # noqa: E402
    incumbent_skill,
    trigger_composition_table,
)
from rs_spy.bias.buckets import BULL, LONG_TRIGGER, NEUTRAL, STRONG_BEAR  # noqa: E402


def test_incumbent_skill_scores_buckets_with_the_same_metric():
    close = _series([100, 200, 100, 50], start="2024-01-02 14:30")
    bias = pd.Series([BULL, NEUTRAL, STRONG_BEAR, STRONG_BEAR],
                     index=close.index, dtype=object)
    table, scores = incumbent_skill(bias, close, horizons=(1,))
    bull = table[(table.state == "BULL_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bull["n"] == 1 and bull["mean_fwd_return"] == pytest.approx(1.0)
    # NEUTRAL bar contributes nowhere; separation keys exist only for 12/24/78,
    # so with horizons=(1,) all sep_* are None -- the table rows are the check here.
    assert scores["sep_24"] is None
    bear = table[(table.state == "BEAR_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bear["n"] == 1 and bear["mean_fwd_return"] == pytest.approx(-0.5)


def test_trigger_composition_conditions_long_triggers_on_state():
    close = _series([100, 110, 121, 133.1], start="2024-01-02 14:30")
    trigger = pd.Series([LONG_TRIGGER, "NONE", LONG_TRIGGER, "NONE"],
                        index=close.index, dtype=object)
    states = pd.Series(["BULL_RUN", "BULL_RUN", "BEAR_RUN", "BEAR_RUN"],
                       index=close.index, dtype=object)
    table = trigger_composition_table(trigger, states, close, horizons=(1,))
    allrow = table[(table.state == "ALL") & (table.horizon_bars == 1)].iloc[0]
    assert allrow["n"] == 2
    bull = table[(table.state == "BULL_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bull["n"] == 1 and bull["mean_fwd_return"] == pytest.approx(0.10)
    bear = table[(table.state == "BEAR_RUN") & (table.horizon_bars == 1)].iloc[0]
    assert bear["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q`
Expected: new tests FAIL with ImportError.

- [ ] **Step 3: Implement**

Append to `src/rs_spy/backtest/studies/oscillator_skill_m5.py`:

```python
from rs_spy.bias.buckets import (  # noqa: E402  (module has a docstring header above)
    BULL,
    LONG_TRIGGER,
    STRONG_BEAR,
    STRONG_BULL,
)
from rs_spy.bias.buckets import BEAR as BEAR_BUCKET  # noqa: E402

INCUMBENT_BULL = (STRONG_BULL, BULL)
INCUMBENT_BEAR = (STRONG_BEAR, BEAR_BUCKET)


def incumbent_skill(
    bias: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> tuple[pd.DataFrame, dict]:
    """Score the current bias engine's buckets with the oscillator's metric.

    Bucket -> state mapping: bull buckets -> BULL_RUN, bear buckets -> BEAR_RUN,
    NEUTRAL/other -> NaN (excluded). The EARLY states stay empty -- the
    incumbent has no equivalent; separation_scores handles the n=0 sides via
    the n-weighted means (weight 0)."""
    mapped = pd.Series(float("nan"), index=bias.index, dtype=object)
    mapped[bias.isin(INCUMBENT_BULL)] = "BULL_RUN"
    mapped[bias.isin(INCUMBENT_BEAR)] = "BEAR_RUN"
    table = state_skill_table(mapped, close, horizons)
    return table, separation_scores(table)


def trigger_composition_table(
    trigger: pd.Series, states: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    """Forward returns of LONG_TRIGGER events, unconditioned (ALL) and
    conditioned on the oscillator state at the trigger bar -- the
    decision-relevant read, since real entries need trigger-in-window
    coincidence."""
    is_long = trigger == LONG_TRIGGER
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        rows.append(
            {"state": "ALL", "horizon_bars": horizon, **_fwd_stats(fwd, is_long)}
        )
        for state in (*BULL_STATES, *BEAR_STATES):
            rows.append(
                {"state": state, "horizon_bars": horizon,
                 **_fwd_stats(fwd, is_long & (states == state))}
            )
    return pd.DataFrame(rows, dtype=object)
```

(If ruff objects to mid-file imports (E402), move them to the top import block instead —
either placement is acceptable; keep ruff clean.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q` — Expected: all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/backtest/studies/oscillator_skill_m5.py tests/unit/test_oscillator_skill.py
git commit -m "M11: incumbent baseline scorer + trigger-composition table"
```

---

### Task 4: Grid, winner selection, holdout verdict, driver script

**Files:**
- Modify: `src/rs_spy/backtest/studies/oscillator_skill_m5.py` (append)
- Create: `scripts/run_oscillator_study.py`
- Test: `tests/unit/test_oscillator_skill.py` (append)

**Interfaces:**
- Consumes: Task 1's `OscSpec/compute_oscillator/oscillator_states/oscillator_crosses`; Tasks 2-3's functions; `bias/engine.bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5) -> DataFrame` (columns incl. `bias`, `trigger`); `data.loader.load_m5_bars/load_minute_bars/load_daily_bars`; `data.resample` (M1→M5 is already what `load_m5_bars` returns — the driver loads exactly like `scripts/run_trigger_skill_study.py` does; read that script and mirror its loading).
- Produces: `candidate_grid() -> list[OscSpec]` (the 24 spec candidates, exact grid from Global Constraints); `TRAIN_MIN_STATE_N = 200`; `HOLDOUT_MIN_STATE_N = 50`; `run_train_sweep(m5: pd.DataFrame, specs: list[OscSpec]) -> tuple[pd.DataFrame, OscSpec | None]` — for each spec: oscillator → states on the TRAIN window only (`split_train_holdout` applied to the state series and close), state table + separation; returns (results frame with one summary row per candidate: `name, input_mode, fast, slow, signal, sep_12, sep_24, sep_78, min_state_n, eligible`, and the winner = highest `sep_24` among eligible, tie-break `sep_12`, None if none eligible); `holdout_verdict(winner_scores: dict, incumbent_scores: dict, train_sep_24: float) -> dict` — pure gate: `{"pass": bool, "checks": {"sep_24_pos": bool, "sep_12_pos": bool, "beats_incumbent": bool, "sign_consistent": bool, "min_n_ok": bool}}` per the pre-committed criteria (min_n from `winner_scores["min_state_n"] >= HOLDOUT_MIN_STATE_N`).
- CLI: `python scripts/run_oscillator_study.py train` (loads SPY/QQQ from the warehouse read-only, runs the sweep on the train window, writes `reports/tuning/oscillator_skill_train.csv`, prints the top-10 candidates + the winner's per-state practice table + the incumbent's train scores + the winner's trigger-composition table); `python scripts/run_oscillator_study.py holdout --spec <name>` (refuses anything but exactly one name; computes that spec + incumbent + composition on the HOLDOUT window; writes `reports/tuning/oscillator_skill_holdout.csv`; prints the verdict PASS/FAIL with each check).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_oscillator_skill.py`:

```python
from rs_spy.backtest.studies.oscillator_skill_m5 import (  # noqa: E402
    HOLDOUT_MIN_STATE_N,
    TRAIN_MIN_STATE_N,
    candidate_grid,
    holdout_verdict,
    run_train_sweep,
)
from rs_spy.indicators.cycle_oscillator import OscSpec  # noqa: E402


def test_candidate_grid_is_the_spec_grid():
    grid = candidate_grid()
    assert len(grid) == 24
    names = {s.name for s in grid}
    assert "close-12-26-9" in names and "vwap_dev-6-13-5" in names
    assert all(s.input_mode in ("close", "vwap_dev") for s in grid)


def _train_m5(n=6000):
    # deterministic sine-trend price so states populate; volume constant
    idx = pd.date_range("2024-01-02 14:30", periods=n, freq="5min", tz="UTC")
    t = np.arange(n)
    close = pd.Series(100 + 5 * np.sin(t / 60) + t * 0.001, index=idx)
    return pd.DataFrame({"open": close, "high": close + 0.1, "low": close - 0.1,
                         "close": close, "volume": 1000.0})


def test_run_train_sweep_returns_summary_and_picks_eligible_max_sep24():
    m5 = _train_m5()
    specs = [OscSpec("close", 6, 13, 5), OscSpec("close", 12, 26, 9)]
    results, winner = run_train_sweep(m5, specs)
    assert set(results["name"]) == {"close-6-13-5", "close-12-26-9"}
    assert {"sep_12", "sep_24", "sep_78", "eligible"} <= set(results.columns)
    eligible = results[results["eligible"] == True]  # noqa: E712
    if winner is not None:
        top = eligible.sort_values(
            ["sep_24", "sep_12"], ascending=False).iloc[0]
        assert winner.name == top["name"]


def test_run_train_sweep_uses_train_window_only():
    # data extending past TRAIN_END must not change the result
    m5 = _train_m5()
    extra_idx = pd.date_range("2026-01-05 14:30", periods=500, freq="5min", tz="UTC")
    extra = pd.DataFrame(
        {"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1000.0},
        index=extra_idx,
    )
    specs = [OscSpec("close", 6, 13, 5)]
    r1, _ = run_train_sweep(m5, specs)
    r2, _ = run_train_sweep(pd.concat([m5, extra]), specs)
    assert r1.loc[0, "sep_24"] == pytest.approx(r2.loc[0, "sep_24"])


def test_holdout_verdict_all_checks():
    ok = holdout_verdict(
        {"sep_12": 0.001, "sep_24": 0.002, "min_state_n": 60},
        {"sep_24": 0.0005},
        train_sep_24=0.003,
    )
    assert ok["pass"] is True and all(ok["checks"].values())
    bad = holdout_verdict(
        {"sep_12": 0.001, "sep_24": -0.002, "min_state_n": 60},
        {"sep_24": 0.0005},
        train_sep_24=0.003,
    )
    assert bad["pass"] is False
    assert bad["checks"]["sep_24_pos"] is False
    assert bad["checks"]["sign_consistent"] is False
    thin = holdout_verdict(
        {"sep_12": 0.001, "sep_24": 0.002, "min_state_n": 10},
        {"sep_24": 0.0},
        train_sep_24=0.003,
    )
    assert thin["pass"] is False and thin["checks"]["min_n_ok"] is False


def test_constants():
    assert TRAIN_MIN_STATE_N == 200 and HOLDOUT_MIN_STATE_N == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q` — Expected: ImportError on the new names.

- [ ] **Step 3: Implement**

Append to `src/rs_spy/backtest/studies/oscillator_skill_m5.py`:

```python
from rs_spy.indicators.cycle_oscillator import (  # noqa: E402
    OscSpec,
    compute_oscillator,
    oscillator_states,
)

TRAIN_MIN_STATE_N = 200
HOLDOUT_MIN_STATE_N = 50
_GRID_PAIRS = ((6, 13), (9, 21), (12, 26), (16, 36))
_GRID_SIGNALS = (5, 9, 13)


def candidate_grid() -> list[OscSpec]:
    """The 24 pre-committed candidates. Deliberately modest ('1OP is probably
    simple'); for vwap_dev the slow parameter is formula-unused bookkeeping."""
    return [
        OscSpec(mode, fast, slow, signal)
        for mode in ("close", "vwap_dev")
        for (fast, slow) in _GRID_PAIRS
        for signal in _GRID_SIGNALS
    ]


def run_train_sweep(
    m5: pd.DataFrame, specs: list[OscSpec]
) -> tuple[pd.DataFrame, OscSpec | None]:
    """Score every candidate on the TRAIN window only; pick the winner.

    Selection is pre-committed: highest sep_24 among candidates whose every
    state has n >= TRAIN_MIN_STATE_N; tie-break sep_12. Holdout data never
    enters this function's scoring (split happens here, not in the caller,
    so a caller mistake cannot leak holdout bars into selection)."""
    train_m5, _ = split_train_holdout(m5)
    rows = []
    by_name: dict[str, OscSpec] = {}
    for spec in specs:
        osc = compute_oscillator(train_m5, spec)
        states = oscillator_states(osc)
        table = state_skill_table(states, train_m5["close"])
        scores = separation_scores(table)
        eligible = (
            scores["min_state_n"] >= TRAIN_MIN_STATE_N
            and scores["sep_24"] is not None
            and scores["sep_12"] is not None
        )
        rows.append(
            {"name": spec.name, "input_mode": spec.input_mode, "fast": spec.fast,
             "slow": spec.slow, "signal": spec.signal, **scores, "eligible": eligible}
        )
        by_name[spec.name] = spec
    results = pd.DataFrame(rows, dtype=object)
    eligible_rows = results[results["eligible"] == True]  # noqa: E712
    if eligible_rows.empty:
        return results, None
    top = eligible_rows.sort_values(
        ["sep_24", "sep_12"], ascending=[False, False]
    ).iloc[0]
    return results, by_name[top["name"]]


def holdout_verdict(
    winner_scores: dict, incumbent_scores: dict, train_sep_24: float
) -> dict:
    """The pre-committed hard gate (spec: 'no exceptions')."""
    sep_24 = winner_scores.get("sep_24")
    sep_12 = winner_scores.get("sep_12")
    inc_24 = incumbent_scores.get("sep_24")
    checks = {
        "sep_24_pos": sep_24 is not None and sep_24 > 0,
        "sep_12_pos": sep_12 is not None and sep_12 > 0,
        "beats_incumbent": (
            sep_24 is not None and (inc_24 is None or sep_24 > inc_24)
        ),
        "sign_consistent": (
            sep_24 is not None
            and train_sep_24 is not None
            and (sep_24 > 0) == (train_sep_24 > 0)
        ),
        "min_n_ok": winner_scores.get("min_state_n", 0) >= HOLDOUT_MIN_STATE_N,
    }
    return {"pass": all(checks.values()), "checks": checks}
```

Create `scripts/run_oscillator_study.py`:

```python
"""M11 Phase 1 driver: cycle-oscillator skill study on real SPY data.

    python scripts/run_oscillator_study.py train
    python scripts/run_oscillator_study.py holdout --spec close-12-26-9

train: sweeps the 24-candidate grid on 2021->2024, writes
reports/tuning/oscillator_skill_train.csv, prints the leaderboard, the
winner's per-state practice table, the incumbent bias engine scored with the
same metric, and the winner's LONG-trigger composition table.

holdout: SINGLE-SHOT gate (spec 2026-07-05-cycle-oscillator-design.md).
Accepts exactly one --spec name; evaluates it + the incumbent on 2025->2026;
prints PASS/FAIL per pre-committed check; writes
reports/tuning/oscillator_skill_holdout.csv. Running it repeatedly with
different specs burns the holdout -- don't.
"""
from pathlib import Path

import pandas as pd
import typer

from rs_spy.backtest.studies.oscillator_skill_m5 import (
    candidate_grid,
    cross_skill_table,
    holdout_verdict,
    incumbent_skill,
    run_train_sweep,
    separation_scores,
    split_train_holdout,
    state_skill_table,
    trigger_composition_table,
)
from rs_spy.bias.engine import bias_series
from rs_spy.config import get_settings
from rs_spy.data.loader import load_daily_bars, load_m5_bars, load_minute_bars
from rs_spy.data.warehouse import connect
from rs_spy.indicators.cycle_oscillator import (
    compute_oscillator,
    oscillator_crosses,
    oscillator_states,
)

app = typer.Typer()
OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "tuning"


def _load_frames():
    settings = get_settings()
    con = connect(settings.resolved_warehouse_path(), read_only=True)
    try:
        spy_m1 = load_minute_bars(con, "SPY")
        spy_m5 = load_m5_bars(con, "SPY")
        spy_d1 = load_daily_bars(con, "SPY")
        qqq_m1 = load_minute_bars(con, "QQQ")
        qqq_m5 = load_m5_bars(con, "QQQ")
    finally:
        con.close()
    return spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5


def _print_table(title: str, df: pd.DataFrame) -> None:
    typer.echo(f"\n== {title} ==")
    typer.echo(df.to_string(index=False))


@app.command()
def train() -> None:
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _load_frames()
    results, winner = run_train_sweep(spy_m5, candidate_grid())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUT_DIR / "oscillator_skill_train.csv", index=False)
    _print_table(
        "leaderboard (train)",
        results.sort_values("sep_24", ascending=False).head(10),
    )
    if winner is None:
        typer.echo("NO ELIGIBLE CANDIDATE -- study ends here (null result).")
        raise typer.Exit(code=1)
    typer.echo(f"\nWINNER (train): {winner.name}")

    train_m5, _ = split_train_holdout(spy_m5)
    osc = compute_oscillator(train_m5, winner)
    states = oscillator_states(osc)
    _print_table("winner per-state (train)", state_skill_table(states, train_m5["close"]))
    _print_table("winner crosses (train)",
                 cross_skill_table(oscillator_crosses(osc), train_m5["close"]))

    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    train_bias, _ = split_train_holdout(bias_df)
    inc_table, inc_scores = incumbent_skill(train_bias["bias"], train_m5["close"])
    _print_table("incumbent buckets (train, same metric)", inc_table)
    typer.echo(f"incumbent separation (train): {inc_scores}")

    comp = trigger_composition_table(train_bias["trigger"], states, train_m5["close"])
    _print_table("LONG-trigger composition by winner state (train)", comp)
    typer.echo(
        "\nNext: python scripts/run_oscillator_study.py holdout --spec " + winner.name
    )


@app.command()
def holdout(spec: str = typer.Option(...)) -> None:
    grid = {s.name: s for s in candidate_grid()}
    if spec not in grid:
        raise typer.BadParameter(
            f"unknown spec {spec!r}; must be one of the 24 grid names"
        )
    chosen = grid[spec]

    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _load_frames()
    _, holdout_m5 = split_train_holdout(spy_m5)
    train_m5, _ = split_train_holdout(spy_m5)

    # train sep_24 for the sign-consistency check (recomputed, train data only)
    t_osc = compute_oscillator(train_m5, chosen)
    t_scores = separation_scores(
        state_skill_table(oscillator_states(t_osc), train_m5["close"])
    )

    osc = compute_oscillator(holdout_m5, chosen)
    states = oscillator_states(osc)
    table = state_skill_table(states, holdout_m5["close"])
    scores = separation_scores(table)

    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    _, holdout_bias = split_train_holdout(bias_df)
    inc_table, inc_scores = incumbent_skill(holdout_bias["bias"], holdout_m5["close"])

    verdict = holdout_verdict(scores, inc_scores, t_scores["sep_24"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = pd.concat(
        [table.assign(who=spec), inc_table.assign(who="incumbent")], ignore_index=True
    )
    out.to_csv(OUT_DIR / "oscillator_skill_holdout.csv", index=False)

    _print_table(f"{spec} per-state (holdout)", table)
    _print_table("incumbent (holdout, same metric)", inc_table)
    comp = trigger_composition_table(holdout_bias["trigger"], states, holdout_m5["close"])
    _print_table("LONG-trigger composition (holdout)", comp)
    typer.echo(f"\nwinner scores:    {scores}")
    typer.echo(f"incumbent scores: {inc_scores}")
    typer.echo(f"train sep_24:     {t_scores['sep_24']}")
    for check, ok in verdict["checks"].items():
        typer.echo(f"  {'PASS' if ok else 'FAIL'}  {check}")
    typer.echo(f"\nVERDICT: {'PASS -- Phase 2 unlocked' if verdict['pass'] else 'FAIL -- null result, keep current engine'}")
    raise typer.Exit(code=0 if verdict["pass"] else 1)


if __name__ == "__main__":
    app()
```

(Note: the oscillator for holdout is computed on the holdout slice only — the EMA warms up
from the first holdout bar rather than carrying 2024 state in. That is a deliberate,
conservative choice: it slightly handicaps the candidate at the window boundary and cannot
leak train information. Document this in the module docstring if not already clear.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_oscillator_skill.py -q` — Expected: all pass.
Full suite + `ruff check .` — green + clean.
The driver script is NOT executed here (real data — Task 5).

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/backtest/studies/oscillator_skill_m5.py \
        scripts/run_oscillator_study.py tests/unit/test_oscillator_skill.py
git commit -m "M11: candidate grid, train sweep + winner, holdout gate + driver"
```

---

### Task 5: Real-data execution + docs (controller/operator-run)

- [ ] **Step 1: Train sweep.** `python scripts/run_oscillator_study.py train` (warehouse
  read-only; ~24 candidates over ~5y of SPY M5 ≈ fast). Save the printed leaderboard +
  practice tables; `reports/tuning/oscillator_skill_train.csv` written.
- [ ] **Step 2: Holdout, once.** `python scripts/run_oscillator_study.py holdout --spec
  <winner-name>`. Record the verdict verbatim. This is the single shot — no re-runs with
  other specs regardless of outcome.
- [ ] **Step 3: Ledger + docs.** `docs/tuning/ledger.csv` rows (train winner + holdout
  verdict); IMPLEMENTATION.md "M11 Phase 1" section (what was built, the leaderboard
  headlines, the incumbent comparison, the per-state practice tables summary, the verdict,
  and — pass or fail — what happens next per the spec's hard gate). CLAUDE.md how-to-run row
  for `run_oscillator_study.py`.
- [ ] **Step 4: Final verification + commit.** `python -m pytest -q -m "not integration"` +
  `ruff check .` green/clean; commit docs on the branch. The branch is NOT merged — the
  morning review decides that.

---

## Self-review notes (spec coverage)

- Indicator (2 modes, 4 states, crosses, causal, modular OscSpec API) → Task 1.
- Study (train/holdout enforced split, state/cross tables, separation metric exactly as
  pre-committed, eligibility floors) → Task 2 + 4.
- Incumbent scored with identical metric; trigger composition → Task 3.
- Winner selection train-only (split inside `run_train_sweep`, tested with a
  data-past-TRAIN-END invariance test) → Task 4.
- Holdout single-shot (one `--spec`, verdict function pure + tested, exit code carries the
  gate) → Task 4; executed once in Task 5.
- Artifacts (2 CSVs, practice tables, ledger, IMPLEMENTATION.md) → Tasks 4-5.
- No engine wiring, no BacktestConfigM5 change, branch-only commits → Global Constraints.
- "Simple valve" guidance honored: 24 candidates, two families, no exotic composites.
