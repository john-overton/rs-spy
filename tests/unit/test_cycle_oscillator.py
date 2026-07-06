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
