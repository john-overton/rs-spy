import pandas as pd

from rs_spy.algo.long import (
    confirm_trigger_entry_long,
    dip_quality_pass_long,
    market_flip_exit_long,
    momentum_stall_long,
    not_extended_long,
    rs_failure_long,
    vwap_loss_long,
)
from rs_spy.bias.buckets import BEAR, BULL


def _idx(n):
    return pd.date_range("2026-01-05 14:30", periods=n, freq="5min", tz="UTC")


def test_not_extended_long():
    close = pd.Series([102.0, 105.0], index=_idx(2))
    ema8 = pd.Series([100.0, 100.0], index=_idx(2))
    atr = pd.Series([5.0, 5.0], index=_idx(2))
    result = not_extended_long(close, ema8, atr)
    assert result.iloc[0]  # 102-100=2 <= 5
    assert result.iloc[1]  # 105-100=5 <= 5 (boundary, inclusive)


def test_confirm_trigger_entry_long_requires_all_three_conditions():
    idx = _idx(1)
    features = pd.DataFrame({"rolling_rrs_m5": [1.2], "close": [101.0], "vwap_m5": [100.0]}, index=idx)
    ema8 = pd.Series([99.0], index=idx)
    atr = pd.Series([5.0], index=idx)
    assert confirm_trigger_entry_long(features, ema8, atr).iloc[0]

    features_weak_rrs = features.assign(rolling_rrs_m5=[0.5])
    assert not confirm_trigger_entry_long(features_weak_rrs, ema8, atr).iloc[0]

    features_below_vwap = features.assign(close=[99.0])
    assert not confirm_trigger_entry_long(features_below_vwap, ema8, atr).iloc[0]

    atr_tiny = pd.Series([0.5], index=idx)  # close-ema8=2 > 1.0*0.5 -> extended
    assert not confirm_trigger_entry_long(features, ema8, atr_tiny).iloc[0]


def test_dip_quality_pass_long_passes_a_healthy_mixed_low_volume_pullback():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [110, 109, 108.5, 108, 108.2, 108.5],
            "high": [111, 109.5, 109, 108.5, 108.8, 109],
            "low": [109, 108, 107.5, 107.5, 107.8, 108],
            "close": [109.5, 108.5, 108, 108.3, 108.5, 108.8],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame(
        {"rvol_m5": [0.6, 0.6, 0.5, 0.6, 0.6, 0.6], "vwap_m5": [107.0] * 6},
        index=idx,
    )
    atr = pd.Series([2.0] * 6, index=idx)
    result = dip_quality_pass_long(df_m5, features, atr)
    assert result.iloc[-1]


def test_dip_quality_pass_long_fails_on_stacked_red_heavy_volume():
    idx = _idx(6)
    df_m5 = pd.DataFrame(
        {
            "open": [110, 109, 108, 107, 106, 105],
            "high": [110.1, 109.1, 108.1, 107.1, 106.1, 105.1],
            "low": [108.9, 107.9, 106.9, 105.9, 104.9, 103.9],
            "close": [109, 108, 107, 106, 105, 104],
            "volume": [1000] * 6,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [2.0] * 6, "vwap_m5": [107.0] * 6}, index=idx)
    atr = pd.Series([1.0] * 6, index=idx)
    result = dip_quality_pass_long(df_m5, features, atr)
    assert not result.iloc[-1]


def test_rs_failure_long_requires_two_consecutive_negative_bars():
    rrs = pd.Series([1.0, -0.5, -0.2, 0.1], index=_idx(4))
    result = rs_failure_long(rrs)
    assert list(result) == [False, False, True, False]


def test_vwap_loss_long_requires_two_consecutive_closes_below():
    close = pd.Series([101.0, 99.0, 98.0, 102.0], index=_idx(4))
    vwap = pd.Series([100.0] * 4, index=_idx(4))
    result = vwap_loss_long(close, vwap)
    assert list(result) == [False, False, True, False]


def test_momentum_stall_long_fires_on_cross_down_through_80():
    lrsi = pd.Series([75.0, 85.0, 78.0, 90.0], index=_idx(4))
    result = momentum_stall_long(lrsi)
    assert list(result) == [False, False, True, False]


def test_market_flip_exit_long_only_on_down_flip():
    idx = _idx(2)
    bias = pd.Series([BULL, BEAR], index=idx)
    flip = pd.Series([False, True], index=idx)
    result = market_flip_exit_long(bias, flip)
    assert list(result) == [False, True]

    bias_up = pd.Series([BEAR, BULL], index=idx)
    flip_up = pd.Series([False, True], index=idx)
    result_up = market_flip_exit_long(bias_up, flip_up)
    assert list(result_up) == [False, False]
