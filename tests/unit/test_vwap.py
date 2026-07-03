import numpy as np
import pandas as pd

from rs_spy.indicators.vwap import vwap

# Two sessions, 3 and 2 bars. Hand-computed:
# Session 1 (2024-01-02):
#   bar0: typical=(10+8+9)/3=9,  vol=100 -> pv=900;   cum_pv=900,  cum_vol=100  -> vwap=9.0
#   bar1: typical=(11+9+10)/3=10, vol=200 -> pv=2000; cum_pv=2900, cum_vol=300  -> vwap=9.6667
#   bar2: typical=(12+10+11)/3=11,vol=100 -> pv=1100; cum_pv=4000, cum_vol=400  -> vwap=10.0
# Session 2 (2024-01-03), resets:
#   bar0: typical=(20+18+19)/3=19, vol=50 -> pv=950;  cum_pv=950,  cum_vol=50   -> vwap=19.0
#   bar1: typical=(21+19+20)/3=20, vol=50 -> pv=1000; cum_pv=1950, cum_vol=100  -> vwap=19.5
_ROWS = [
    (10, 8, 9, 100),
    (11, 9, 10, 200),
    (12, 10, 11, 100),
    (20, 18, 19, 50),
    (21, 19, 20, 50),
]
_INDEX = pd.to_datetime(
    [
        "2024-01-02 14:30",
        "2024-01-02 14:31",
        "2024-01-02 14:32",
        "2024-01-03 14:30",
        "2024-01-03 14:31",
    ],
    utc=True,
)
_EXPECTED = [9.0, 2900 / 300, 10.0, 19.0, 19.5]


def _df():
    return pd.DataFrame(_ROWS, columns=["high", "low", "close", "volume"], index=_INDEX)


def test_vwap_golden():
    result = vwap(_df())
    np.testing.assert_allclose(result.to_numpy(), _EXPECTED, rtol=1e-9)


def test_vwap_resets_each_session():
    result = vwap(_df())
    # session 2's first bar's vwap equals its own typical price, unaffected
    # by session 1's cumulative totals.
    assert result.iloc[3] == 19.0
