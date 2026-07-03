import numpy as np
import pandas as pd
import pytest

from rs_spy.indicators.candle_structure import (
    body_pct,
    chop_ratio,
    follow_through,
    overlap_ratio,
    stacked_count,
)

# Hand-computed golden fixture. volume_ratio_d1 uses lookback=2 (small, for
# testability): avg_vol[t] = mean(volume[t-2], volume[t-1]).
#
#              O      H      L      C     volume   body_pct   vol_ratio
# idx0:      10.0   12.0   10.0   11.8    100       0.90        NaN (insufficient history)
# idx1:      11.8   13.8   11.8   13.6    150       0.90        NaN (insufficient history)
# idx2:      13.6   15.6   13.6   15.4    200       0.90        200/mean(100,150)=200/125=1.60  -> qualifies, streak=1
# idx3:      15.4   15.6   13.0   13.2    180       0.846       180/mean(150,200)=180/175=1.029 -> fails volume, streak=0
# idx4:      13.2   13.4   13.0   13.3     50       0.25        50/mean(200,180)=50/190=0.263   -> fails both, streak=0
# idx5:      13.3   15.3   13.3   15.1    300       0.90        300/mean(180,50)=300/115=2.609  -> qualifies, streak=1 (fresh)
# idx6:      15.1   17.1   15.1   16.9    300       0.90        300/mean(50,300)=300/175=1.714  -> qualifies, streak=2 (continuation)
_ROWS = [
    (10.0, 12.0, 10.0, 11.8, 100),
    (11.8, 13.8, 11.8, 13.6, 150),
    (13.6, 15.6, 13.6, 15.4, 200),
    (15.4, 15.6, 13.0, 13.2, 180),
    (13.2, 13.4, 13.0, 13.3, 50),
    (13.3, 15.3, 13.3, 15.1, 300),
    (15.1, 17.1, 15.1, 16.9, 300),
]
_EXPECTED_STACKED = [0, 0, 1, 0, 0, 1, 2]


def _df():
    return pd.DataFrame(_ROWS, columns=["open", "high", "low", "close", "volume"])


def test_stacked_count_golden():
    result = stacked_count(_df(), min_body_pct=0.6, min_volume_ratio=1.2, volume_lookback=2)
    np.testing.assert_array_equal(result.to_numpy(), _EXPECTED_STACKED)


def test_overlap_ratio_golden():
    df = _df()
    result = overlap_ratio(df)
    assert np.isnan(result.iloc[0])
    # idx1: range=[11.8,13.8], prev range=[10,12] -> intersection=12-11.8=0.2, range width 2.0
    assert result.iloc[1] == pytest.approx(0.1)
    # idx2: range=[13.6,15.6], prev=[11.8,13.8] -> intersection=13.8-13.6=0.2, width 2.0
    assert result.iloc[2] == pytest.approx(0.1)
    # idx3: range=[13.0,15.6], prev=[13.6,15.6] -> intersection=15.6-13.6=2.0, width 2.6
    assert result.iloc[3] == pytest.approx(2.0 / 2.6)


def test_follow_through_confirms_when_closes_hold_above_midpoint():
    df = _df()
    volume_ratio = pd.Series([np.nan, np.nan, 1.6, 1.029, 0.263, 2.609, 1.714])
    # breakout_idx=0: open=10.0 close=11.8 -> midpoint=10.9
    # next 3 closes: 13.6, 15.4, 13.2 -- all > 10.9, and window volume_ratio has values >= 1.0
    assert follow_through(df, breakout_idx=0, volume_ratio=volume_ratio, n_sessions=3) is True


def test_follow_through_fails_when_closes_dont_hold():
    df = _df()
    volume_ratio = pd.Series([np.nan, np.nan, 1.6, 1.029, 0.263, 2.609, 1.714])
    # breakout_idx=2: midpoint=(13.6+15.4)/2=14.5; next closes 13.2, 13.3, 15.1 -- not all above
    assert follow_through(df, breakout_idx=2, volume_ratio=volume_ratio, n_sessions=3) is False


def test_follow_through_false_when_insufficient_future_history():
    df = _df()
    volume_ratio = pd.Series([np.nan] * len(df))
    assert follow_through(df, breakout_idx=6, volume_ratio=volume_ratio, n_sessions=3) is False


# Regression fixture for a real zero-range bar (open == high == low == close),
# a legitimate market condition confirmed in real cached SPY M5 data (34 such
# bars in 5 years of history, e.g. a single trade printing at one exact price
# for the whole window during an illiquid period). idx2 below is such a bar.
# `.replace(0, pd.NA)` (the pre-fix code) silently upcasts the whole Series to
# object dtype the moment it hits a zero-range bar, which later breaks
# `chop_ratio`'s `.rolling(window).mean()` with
# `pandas.errors.DataError: No numeric types to aggregate`.
_ZERO_RANGE_ROWS = [
    (10.0, 12.0, 10.0, 11.0, 100),
    (11.0, 13.0, 11.0, 12.5, 150),
    (12.5, 12.5, 12.5, 12.5, 200),  # zero-range bar: high == low (== open == close)
    (12.5, 14.0, 12.0, 13.5, 180),
    (13.5, 15.0, 13.0, 14.5, 150),
    (14.5, 16.0, 14.0, 15.5, 300),
]


def _zero_range_df():
    return pd.DataFrame(_ZERO_RANGE_ROWS, columns=["open", "high", "low", "close", "volume"])


def test_zero_range_bar_does_not_upcast_to_object_dtype():
    """A real high==low bar must not silently upcast these Series to object
    dtype (pd.NA does this; np.nan does not), and must not crash downstream
    rolling aggregations. The zero-range bar's own row should be NaN
    (division-by-zero guarded), while other rows stay finite and unaffected."""
    df = _zero_range_df()

    bp = body_pct(df)
    orat = overlap_ratio(df)
    chop = chop_ratio(df)
    stacked = stacked_count(df, min_body_pct=0.6, min_volume_ratio=1.2, volume_lookback=2)

    # dtype must stay numeric float64, never fall back to object.
    assert bp.dtype == np.float64, bp.dtype
    assert orat.dtype == np.float64, orat.dtype
    assert chop.dtype == np.float64, chop.dtype
    assert stacked.dtype != object, stacked.dtype

    # The zero-range bar (idx2) is division-by-zero guarded -> NaN, not 0/inf.
    assert np.isnan(bp.iloc[2])
    assert np.isnan(orat.iloc[2])

    # Other rows are unaffected and produce valid finite values.
    for i in (0, 1, 3, 4, 5):
        assert np.isfinite(bp.iloc[i]), f"body_pct row {i} should be finite"
    for i in (1, 3, 4, 5):
        assert np.isfinite(orat.iloc[i]), f"overlap_ratio row {i} should be finite"
