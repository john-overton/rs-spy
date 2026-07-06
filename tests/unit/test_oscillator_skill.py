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
