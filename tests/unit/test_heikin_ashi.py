import numpy as np
import pandas as pd

from rs_spy.indicators.heikin_ashi import ha_continuation, heikin_ashi

# Hand-computed golden fixture. Bars 0-3 are O=L, H=C ("clean" up days, no
# wicks on the raw candle) with a large jump each bar, which keeps the
# lagging HA_Open average well below the raw low -- so HA_Open ends up
# exactly equal to HA_Low (flat bottom) with zero tolerance needed:
#
# bar0: O=10 H=12 L=10 C=12 -> HA_Close=(10+12+10+12)/4=11.0
#       HA_Open = (O0+C0)/2 = 11.0  (seed; HA_Close==HA_Open -> doji, day_type=0)
#       HA_High=max(12,11,11)=12   HA_Low=min(10,11,11)=10
# bar1: O=12 H=15 L=12 C=15 -> HA_Close=(12+15+12+15)/4=13.5
#       HA_Open=(11.0+11.0)/2=11.0
#       HA_High=max(15,11,13.5)=15  HA_Low=min(12,11,13.5)=11.0 == HA_Open (flat bottom, bullish)
# bar2: O=15 H=18 L=15 C=18 -> HA_Close=(15+18+15+18)/4=16.5
#       HA_Open=(11.0+13.5)/2=12.25
#       HA_High=18  HA_Low=min(15,12.25,16.5)=12.25 == HA_Open (flat bottom, bullish)
# bar3: O=18 H=21 L=18 C=21 -> HA_Close=(18+21+18+21)/4=19.5
#       HA_Open=(12.25+16.5)/2=14.375
#       HA_High=21  HA_Low=min(18,14.375,19.5)=14.375 == HA_Open (flat bottom, bullish)
# bar4: O=15 H=16 L=10 C=11 (reversal day) -> HA_Close=(15+16+10+11)/4=13.0
#       HA_Open=(14.375+19.5)/2=16.9375
#       HA_High=max(16,16.9375,13.0)=16.9375 == HA_Open (flat top, bearish)
#       HA_Low=min(10,16.9375,13.0)=10
_ROWS = [
    (10, 12, 10, 12),
    (12, 15, 12, 15),
    (15, 18, 15, 18),
    (18, 21, 18, 21),
    (15, 16, 10, 11),
]
_EXPECTED_HA_OPEN = [11.0, 11.0, 12.25, 14.375, 16.9375]
_EXPECTED_HA_CLOSE = [11.0, 13.5, 16.5, 19.5, 13.0]
_EXPECTED_HA_HIGH = [12.0, 15.0, 18.0, 21.0, 16.9375]
_EXPECTED_HA_LOW = [10.0, 11.0, 12.25, 14.375, 10.0]
_EXPECTED_CONTINUATION = [0, 1, 2, 3, -1]


def _df():
    return pd.DataFrame(_ROWS, columns=["open", "high", "low", "close"])


def test_heikin_ashi_transform_golden():
    ha = heikin_ashi(_df())
    np.testing.assert_allclose(ha["ha_open"].to_numpy(), _EXPECTED_HA_OPEN)
    np.testing.assert_allclose(ha["ha_close"].to_numpy(), _EXPECTED_HA_CLOSE)
    np.testing.assert_allclose(ha["ha_high"].to_numpy(), _EXPECTED_HA_HIGH)
    np.testing.assert_allclose(ha["ha_low"].to_numpy(), _EXPECTED_HA_LOW)


def test_ha_continuation_golden():
    df = _df()
    atr = pd.Series([1.0] * len(df))  # any positive value: golden diffs are exactly 0
    result = ha_continuation(df, atr)
    np.testing.assert_array_equal(result.to_numpy(), _EXPECTED_CONTINUATION)


def test_ha_continuation_nan_atr_does_not_qualify():
    df = _df()
    atr = pd.Series([np.nan] * len(df))
    result = ha_continuation(df, atr)
    assert (result == 0).all()
