import pandas as pd
import pytest

from rs_spy.indicators.trendlines import breach_down, breach_up, down_trendline, up_trendline

# --- down_trendline fixture ----------------------------------------------
# strength=2, min_gap=6. Two confirmed descending pivot highs: idx2 (110)
# and idx9 (105), 7 bars apart (>=min_gap). Pivot j is only usable once
# bar j+strength exists, so the line only starts existing at t=11 (9+2).
_HIGHS = [100, 105, 110, 104, 102, 98, 96, 100, 103, 105, 101, 99, 97, 95]
_DOWN_ROWS = [(h - 2, h, h - 5, h - 1) for h in _HIGHS]
_DOWN_ROWS[12] = (_HIGHS[12] - 2, _HIGHS[12], _HIGHS[12] - 5, 100.5)
_DOWN_ROWS[13] = (_HIGHS[13] - 2, _HIGHS[13], _HIGHS[13] - 5, 103.0)

# --- up_trendline fixture (mirrored) --------------------------------------
_LOWS = [100, 95, 90, 96, 98, 102, 104, 100, 97, 95, 99, 101, 103, 105]
_UP_ROWS = [(lo + 2, lo + 5, lo, lo + 1) for lo in _LOWS]
_UP_ROWS[12] = (_LOWS[12] + 2, _LOWS[12] + 5, _LOWS[12], 98.0)
_UP_ROWS[13] = (_LOWS[13] + 2, _LOWS[13] + 5, _LOWS[13], 97.5)


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_down_trendline_nan_before_two_pivots_confirmed():
    df = _df(_DOWN_ROWS)
    result = down_trendline(df, strength=2, min_gap=6)
    assert result.iloc[:11].isna().all()


def test_down_trendline_value_after_two_pivots_confirmed():
    df = _df(_DOWN_ROWS)
    result = down_trendline(df, strength=2, min_gap=6)
    # slope = (105-110)/(9-2) = -5/7; value(t) = 105 + slope*(t-9)
    slope = -5 / 7
    for t in (11, 12, 13):
        expected = 105 + slope * (t - 9)
        assert result.iloc[t] == pytest.approx(expected)


def test_breach_up_golden():
    df = _df(_DOWN_ROWS)
    line = down_trendline(df, strength=2, min_gap=6)
    atr = pd.Series([1.0] * len(df))
    result = breach_up(df["close"], line, atr, tolerance_mult=0.05)
    assert not result.iloc[12]  # close=100.5, threshold=102.857+0.05
    assert result.iloc[13]  # close=103.0, threshold=102.143+0.05


def test_up_trendline_value_after_two_pivots_confirmed():
    df = _df(_UP_ROWS)
    result = up_trendline(df, strength=2, min_gap=6)
    slope = 5 / 7
    for t in (11, 12, 13):
        expected = 95 + slope * (t - 9)
        assert result.iloc[t] == pytest.approx(expected)


def test_breach_down_golden():
    df = _df(_UP_ROWS)
    line = up_trendline(df, strength=2, min_gap=6)
    atr = pd.Series([1.0] * len(df))
    result = breach_down(df["close"], line, atr, tolerance_mult=0.05)
    assert not result.iloc[12]  # close=98.0, threshold=97.143-0.05
    assert result.iloc[13]  # close=97.5, threshold=97.857-0.05
