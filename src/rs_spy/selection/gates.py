"""D1-available hard gates. algo-spec/04-stock-selection-engine.md §2.

Restricted subset of the full spec's 9 gates (G1-G9): G2 (M5 RS) and G3
(VWAP) have no D1 equivalent and are dropped -- RollingRRS_D1 (using the D1
window, not M5) stands in as the primary qualification signal for this
walking-skeleton milestone. G9 (QQQ cross-check) is deferred to the M5 full
engine. G1 (universe: price/ADV/float) is checked dynamically here even
though the curated universe satisfies it by construction at listing time.
"""
import pandas as pd

from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL


def gate_price(df: pd.DataFrame, min_price: float = 10.0) -> pd.Series:
    return df["close"] >= min_price


def gate_adv(df: pd.DataFrame, min_shares: float = 1_000_000, lookback: int = 20) -> pd.Series:
    adv = df["volume"].rolling(lookback).mean()
    return adv >= min_shares


def gate_rrs_long(features: pd.DataFrame, threshold: float = 1.0, column: str = "rolling_rrs_d1") -> pd.Series:
    return features[column] >= threshold


def gate_rrs_short(features: pd.DataFrame, threshold: float = -1.0, column: str = "rolling_rrs_d1") -> pd.Series:
    return features[column] <= threshold


def gate_ha_long(features: pd.DataFrame, min_days: int = 2) -> pd.Series:
    return features["ha_cont_d1"] >= min_days


def gate_ha_short(features: pd.DataFrame, min_days: int = 2) -> pd.Series:
    return features["ha_cont_d1"] <= -min_days


def gate_sma_long(features: pd.DataFrame) -> pd.Series:
    return features["sma_stack"] == ABOVE_ALL


def gate_sma_short(features: pd.DataFrame) -> pd.Series:
    return features["sma_stack"] == BELOW_ALL


def gate_headroom_long(features: pd.DataFrame, min_atr: float = 1.0) -> pd.Series:
    hr = features["headroom_long"]
    return hr.isna() | (hr >= min_atr)  # NaN = no resistance found = infinite headroom


def gate_headroom_short(features: pd.DataFrame, min_atr: float = 1.0) -> pd.Series:
    hr = features["headroom_short"]
    return hr.isna() | (hr >= min_atr)


def gate_volume(features: pd.DataFrame, min_rvol: float = 1.0) -> pd.Series:
    return features["volume_ratio_d1"] >= min_rvol


def gate_earnings(index: pd.DatetimeIndex, blackout_dates: set) -> pd.Series:
    if not blackout_dates:
        return pd.Series(True, index=index)
    dates = index.normalize()
    return pd.Series(~dates.isin(blackout_dates), index=index)


# Names for the 4 D1-analog "hard rules" the M3.5 ablation study (algo-spec
# 08 §3.1) disables one at a time. "bias" is the outer market-bias filter
# applied in backtest/engine.py, not a gates.py gate, but shares this set so
# callers can pass a single `disabled` set through both layers. "rrs" stands
# in for the spec's VWAP hard rule, per this module's docstring.
HARD_RULE_NAMES = frozenset({"bias", "rrs", "ha", "sma"})


def gates_pass_long(
    df: pd.DataFrame,
    features: pd.DataFrame,
    earnings_blackout: set | None = None,
    min_price: float = 10.0,
    min_adv_shares: float = 1_000_000,
    rrs_threshold: float = 1.0,
    rrs_column: str = "rolling_rrs_d1",
    min_ha_days: int = 2,
    min_headroom_atr: float = 1.0,
    min_rvol: float = 1.0,
    disabled: frozenset = frozenset(),
) -> pd.Series:
    result = gate_price(df, min_price) & gate_adv(df, min_adv_shares) & gate_earnings(
        df.index, earnings_blackout or set()
    )
    if "rrs" not in disabled:
        result &= gate_rrs_long(features, rrs_threshold, rrs_column)
    if "ha" not in disabled:
        result &= gate_ha_long(features, min_ha_days)
    if "sma" not in disabled:
        result &= gate_sma_long(features)
    result &= gate_headroom_long(features, min_headroom_atr)
    result &= gate_volume(features, min_rvol)
    return result


def gates_pass_short(
    df: pd.DataFrame,
    features: pd.DataFrame,
    earnings_blackout: set | None = None,
    min_price: float = 10.0,
    min_adv_shares: float = 1_000_000,
    rrs_threshold: float = -1.0,
    rrs_column: str = "rolling_rrs_d1",
    min_ha_days: int = 2,
    min_headroom_atr: float = 1.0,
    min_rvol: float = 1.0,
    disabled: frozenset = frozenset(),
) -> pd.Series:
    result = gate_price(df, min_price) & gate_adv(df, min_adv_shares) & gate_earnings(
        df.index, earnings_blackout or set()
    )
    if "rrs" not in disabled:
        result &= gate_rrs_short(features, rrs_threshold, rrs_column)
    if "ha" not in disabled:
        result &= gate_ha_short(features, min_ha_days)
    if "sma" not in disabled:
        result &= gate_sma_short(features)
    result &= gate_headroom_short(features, min_headroom_atr)
    result &= gate_volume(features, min_rvol)
    return result
