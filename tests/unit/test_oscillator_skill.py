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
    s = _series(range(10), start="2024-12-31 23:45")  # crosses 2025-01-01 UTC
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


def test_min_state_n_is_per_state_per_horizon():
    # Same state at two horizons, n=100 each: true occupancy is 100, not 200.
    rows = [
        {"state": "BULL_RUN", "horizon_bars": 12, "n": 100,
         "mean_fwd_return": 0.01, "median_fwd_return": 0.01},
        {"state": "BULL_RUN", "horizon_bars": 24, "n": 100,
         "mean_fwd_return": 0.01, "median_fwd_return": 0.01},
    ]
    assert separation_scores(pd.DataFrame(rows))["min_state_n"] == 100


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
