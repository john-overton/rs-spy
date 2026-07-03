import numpy as np
import pandas as pd
import pytest

from rs_spy.indicators.headroom import headroom_long, headroom_short, pivot_highs, pivot_lows

# --- headroom_long fixture ---------------------------------------------
# strength=2 (window=5), so a pivot at index j is only *confirmed* (usable)
# once bar j+2 exists. Highs rise to a clear local-max pivot at idx5=120
# (window idx3..7 = [106,108,120,110,105]), then fall back. close=high-1
# throughout except the final bar, which closes AT its own high to create a
# "no candidate above price" (infinite headroom / NaN) case. SMA periods
# (50/100/200) never populate at this length, so week52-high and the idx5
# pivot are the only candidates in play.
_HIGHS = [100, 102, 104, 106, 108, 120, 110, 105, 100, 98, 95, 130]
_LONG_ROWS = [(h - 3, h, h - 4, (h - 1 if i != 11 else h)) for i, h in enumerate(_HIGHS)]

# --- headroom_short fixture (mirrored) ----------------------------------
_LOWS = [100, 98, 96, 94, 92, 80, 90, 95, 100, 102, 105, 70]
_SHORT_ROWS = [(lo + 3, lo + 4, lo, (lo + 1 if i != 11 else lo)) for i, lo in enumerate(_LOWS)]


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_pivot_highs_detects_single_local_max():
    df = _df(_LONG_ROWS)
    result = pivot_highs(df, strength=2)
    assert result.iloc[5]
    assert not result.iloc[4]
    assert not result.iloc[6]


def test_pivot_lows_detects_single_local_min():
    df = _df(_SHORT_ROWS)
    result = pivot_lows(df, strength=2)
    assert result.iloc[5]
    assert not result.iloc[4]
    assert not result.iloc[6]


def test_headroom_long_before_pivot_confirmed_uses_only_week52_high():
    df = _df(_LONG_ROWS)
    atr = pd.Series([2.0] * len(df))
    result = headroom_long(df, atr, strength=2, lookback=10)
    # t=6: pivot at idx5 needs idx5+2=7 <= t; at t=6 it's not yet confirmed.
    # week52_high(6) = max(highs[0:7]) = 120; close[6] = 109
    assert result.iloc[6] == pytest.approx((120 - 109) / 2.0)


def test_headroom_long_after_pivot_confirmed():
    df = _df(_LONG_ROWS)
    atr = pd.Series([2.0] * len(df))
    result = headroom_long(df, atr, strength=2, lookback=10)
    # t=7: pivot confirmed (5+2=7<=7); nearest candidate still 120; close[7]=104
    assert result.iloc[7] == pytest.approx((120 - 104) / 2.0)
    # t=8: close[8]=99
    assert result.iloc[8] == pytest.approx((120 - 99) / 2.0)


def test_headroom_long_nan_when_no_candidate_above_price():
    df = _df(_LONG_ROWS)
    atr = pd.Series([2.0] * len(df))
    result = headroom_long(df, atr, strength=2, lookback=10)
    # t=11: close == own high == new running week52-high -> nothing strictly above
    assert np.isnan(result.iloc[11])


def test_headroom_short_mirrors_long():
    df = _df(_SHORT_ROWS)
    atr = pd.Series([2.0] * len(df))
    result = headroom_short(df, atr, strength=2, lookback=10)
    assert result.iloc[6] == pytest.approx((91 - 80) / 2.0)
    assert result.iloc[7] == pytest.approx((96 - 80) / 2.0)
    assert result.iloc[8] == pytest.approx((101 - 80) / 2.0)
    assert np.isnan(result.iloc[11])
