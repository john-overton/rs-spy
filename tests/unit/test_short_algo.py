import pandas as pd

from rs_spy.algo.short import (
    bounce_quality_pass_short,
    confirm_trigger_entry_short,
    market_flip_exit_short,
    momentum_stall_short,
    not_extended_short,
    rs_failure_short,
    squeeze_guard_short,
    vwap_loss_short,
)
from rs_spy.bias.buckets import BEAR, BULL


def _idx(n):
    return pd.date_range("2026-01-05 14:30", periods=n, freq="5min", tz="UTC")


def test_not_extended_short():
    close = pd.Series([98.0, 95.0], index=_idx(2))
    ema8 = pd.Series([100.0, 100.0], index=_idx(2))
    atr = pd.Series([5.0, 5.0], index=_idx(2))
    result = not_extended_short(close, ema8, atr)
    assert result.iloc[0]  # 100-98=2 <= 5
    assert result.iloc[1]  # 100-95=5 <= 5


def test_confirm_trigger_entry_short_requires_all_three_conditions():
    idx = _idx(1)
    features = pd.DataFrame({"rolling_rrs_m5": [-1.2], "close": [99.0], "vwap_m5": [100.0]}, index=idx)
    ema8 = pd.Series([101.0], index=idx)
    atr = pd.Series([5.0], index=idx)
    assert confirm_trigger_entry_short(features, ema8, atr).iloc[0]

    weak_rrs = features.assign(rolling_rrs_m5=[-0.5])
    assert not confirm_trigger_entry_short(weak_rrs, ema8, atr).iloc[0]

    above_vwap = features.assign(close=[101.0])
    assert not confirm_trigger_entry_short(above_vwap, ema8, atr).iloc[0]


def test_bounce_quality_pass_short_passes_a_wimpy_low_volume_bounce():
    # 7 bars: 1 warmup bar (index 0) providing prior history for chop_ratio's
    # internal overlap_ratio (which needs a shift(1) predecessor), followed by
    # the 6-bar DIP_PULLBACK_WINDOW pattern under test at indices 1-6. See
    # algo/long.py's dip_quality_pass_long tests for the identical pattern.
    idx = _idx(7)
    df_m5 = pd.DataFrame(
        {
            "open": [93.0, 90, 91, 91.5, 92, 91.8, 91.5],
            "high": [94.0, 91, 92, 92.5, 92.5, 92.2, 92],
            "low": [89.5, 89, 90.5, 91, 91.5, 91.2, 91],
            "close": [90.0, 90.5, 91.5, 92, 91.8, 91.6, 91.3],
            "volume": [1000] * 7,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [1.5, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6], "vwap_m5": [93.0] * 7}, index=idx)
    atr = pd.Series([2.0] * 7, index=idx)
    result = bounce_quality_pass_short(df_m5, features, atr)
    assert result.iloc[-1]


def test_bounce_quality_pass_short_fails_on_stacked_green_heavy_volume():
    # Same warmup-bar rationale as the passing-case fixture above.
    idx = _idx(7)
    df_m5 = pd.DataFrame(
        {
            "open": [88.5, 90, 91, 92, 93, 94, 95],
            "high": [89.6, 91.1, 92.1, 93.1, 94.1, 95.1, 96.1],
            "low": [87.9, 89.9, 90.9, 91.9, 92.9, 93.9, 94.9],
            "close": [89.5, 91, 92, 93, 94, 95, 96],
            "volume": [1000] * 7,
        },
        index=idx,
    )
    features = pd.DataFrame({"rvol_m5": [2.0] * 7, "vwap_m5": [93.0] * 7}, index=idx)
    atr = pd.Series([1.0] * 7, index=idx)
    result = bounce_quality_pass_short(df_m5, features, atr)
    assert not result.iloc[-1]


def test_rs_failure_short_requires_two_consecutive_positive_bars():
    rrs = pd.Series([-1.0, 0.5, 0.2, -0.1], index=_idx(4))
    assert list(rs_failure_short(rrs)) == [False, False, True, False]


def test_vwap_loss_short_requires_two_consecutive_closes_above():
    close = pd.Series([99.0, 101.0, 102.0, 98.0], index=_idx(4))
    vwap = pd.Series([100.0] * 4, index=_idx(4))
    assert list(vwap_loss_short(close, vwap)) == [False, False, True, False]


def test_momentum_stall_short_fires_on_cross_up_through_20():
    lrsi = pd.Series([25.0, 15.0, 22.0, 10.0], index=_idx(4))
    assert list(momentum_stall_short(lrsi)) == [False, False, True, False]


def test_market_flip_exit_short_unconditional_on_bull_flip():
    bias = pd.Series([BEAR, BULL], index=_idx(2))
    assert list(market_flip_exit_short(bias)) == [False, True]


def test_squeeze_guard_short_fires_on_violent_adverse_spike():
    high = pd.Series([102.5], index=_idx(1))
    prev_close = pd.Series([100.0], index=_idx(1))
    atr = pd.Series([1.0], index=_idx(1))
    rvol = pd.Series([2.5], index=_idx(1))
    assert squeeze_guard_short(high, prev_close, atr, rvol).iloc[0]

    rvol_low = pd.Series([1.0], index=_idx(1))
    assert not squeeze_guard_short(high, prev_close, atr, rvol_low).iloc[0]
