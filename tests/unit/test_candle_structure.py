import numpy as np
import pandas as pd
import pytest

from rs_spy.indicators.candle_structure import follow_through, overlap_ratio, stacked_count

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
