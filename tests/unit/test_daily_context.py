import numpy as np
import pandas as pd

from rs_spy.bias.daily_context import daily_context_series
from rs_spy.bias.regime import TREND_UP


def _flat_d1(n=80, base=400.0):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(base, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1_000_000.0},
        index=idx,
    )


def test_prior_day_levels_are_raw_unshifted_columns():
    df = _flat_d1(n=10, base=400.0)
    df.loc[df.index[5], ["high", "low", "close"]] = [410.0, 405.0, 408.0]
    out = daily_context_series(df)
    # row 5 itself carries row 5's own OHLC -- shifting to "yesterday" is the
    # alignment step's job (align_daily_to_intraday), not this function's.
    assert out["d1_high"].iloc[5] == 410.0
    assert out["d1_close"].iloc[5] == 408.0


def test_regime_d1_column_matches_regime_module_output():
    n = 80
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(400.0 + np.arange(n) * 0.5, index=idx)  # steady uptrend
    df = pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1_000_000.0}, index=idx
    )
    out = daily_context_series(df)
    assert out["regime_d1"].iloc[-1] == TREND_UP


def test_flat_market_has_no_suspect_rally():
    df = _flat_d1(n=60)
    out = daily_context_series(df)
    assert not out["suspect_rally"].any()
    assert not out["suspect_selloff"].any()


def _down_trendline_highs(n):
    """Two isolated, decreasing pivot highs (idx 10 -> 410, idx 25 -> 407,
    19 bars apart, >= trendlines.py's min_gap=6) forming a genuine
    down-trendline: confirmed (strength=3) by idx 28, value(t) = 407 - 0.2 *
    (t - 25). By t=40 the line sits at ~404, comfortably below a breakout
    close but still above the flat 400 pre-breakout baseline for the whole
    fixture length, so no organic/premature breach occurs before the
    deliberate breakout bar. A near-flat baseline (401 + tiny strictly
    increasing epsilon, to avoid tied-high plateaus that would otherwise all
    register as degenerate pivots and starve `_fit_line`'s two-point line of
    ever finding a second, sufficiently-separated pivot) fills every other
    bar's high.
    """
    high = 401.0 + np.arange(n) * 0.0001
    high[10] = 410.0
    high[25] = 407.0
    return high


def test_breakout_with_strong_follow_through_is_not_suspect():
    n = 60
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = np.full(n, 400.0)
    # a real breakout at t=40: gap up and hold, then 3 sessions of continued
    # strength on decent volume -- should NOT be flagged suspect.
    close[40:] = 410.0
    close[41:] = np.linspace(411.0, 420.0, n - 41)
    high = np.maximum(_down_trendline_highs(n), close + 1.0)
    df = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": high,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )
    df.loc[df.index[38:42], "volume"] = 3_000_000.0  # confirm on volume
    out = daily_context_series(df)
    # give it a few sessions past the breakout to resolve
    assert not out["suspect_rally"].iloc[45]


def test_breakout_with_no_follow_through_is_suspect():
    n = 60
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = np.full(n, 400.0)
    close[40] = 412.0  # single-session spike breakout
    close[41:] = 400.0 - np.arange(n - 41) * 0.2  # immediately fades back down
    high = np.maximum(_down_trendline_highs(n), close + 1.0)
    df = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": high,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )
    out = daily_context_series(df)
    assert out["suspect_rally"].iloc[42]
