"""Hard gates. algo-spec/04-stock-selection-engine.md §2.

The D1-cadence gates below (gate_price through gates_pass_short) are the
restricted subset used by the D1 walking skeleton (M3): G2 (M5 RS) and G3
(VWAP) have no D1 equivalent there. The M5-cadence additions at the bottom
(gate_vwap_*, gate_rrs_m5_*, gates_pass_*_m5) complete the full 9-gate set
(G1-G9) plus 04 §3's anti-pattern exclusions, for use with
selection/features_m5.py's output.
"""
import pandas as pd

from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL


def gate_price(df: pd.DataFrame, min_price: float = 10.0) -> pd.Series:
    return df["close"] >= min_price


def gate_adv(
    df: pd.DataFrame, min_shares: float = 1_000_000, lookback: int = 20,
    adv: pd.Series | None = None,
) -> pd.Series:
    """`adv`, if given, is an already-computed ADV series (e.g. a
    daily-cadence 20-day average aligned onto this df's own index) compared
    directly against `min_shares`, bypassing the rolling-mean computation
    entirely -- required at M5 cadence, where a rolling mean of `df`'s own
    5-minute-bar volume is not a 20-day average. Without it, falls back to a
    rolling(lookback) mean of `df["volume"]`, correct when `df` is itself
    daily-cadence (the D1 walking-skeleton's usage)."""
    if adv is None:
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
HARD_RULE_NAMES = frozenset({"bias", "rrs", "ha", "sma", "rrs_m5", "vwap"})


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
    adv20: pd.Series | None = None,
) -> pd.Series:
    result = gate_price(df, min_price) & gate_adv(df, min_adv_shares, adv=adv20) & gate_earnings(
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
    adv20: pd.Series | None = None,
) -> pd.Series:
    result = gate_price(df, min_price) & gate_adv(df, min_adv_shares, adv=adv20) & gate_earnings(
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


def gate_vwap_long(features: pd.DataFrame) -> pd.Series:
    return features["close"] > features["vwap_m5"]


def gate_vwap_short(features: pd.DataFrame) -> pd.Series:
    return features["close"] < features["vwap_m5"]


def gate_rrs_m5_long(features: pd.DataFrame, threshold: float = 1.0) -> pd.Series:
    return features["rolling_rrs_m5"] >= threshold


def gate_rrs_m5_short(features: pd.DataFrame, threshold: float = -1.0) -> pd.Series:
    return features["rolling_rrs_m5"] <= threshold


def gate_not_one_candle_wonder(features: pd.DataFrame) -> pd.Series:
    """04 §3 anti-pattern: a single M5 bar dominating >60% of the RRS
    window's price change is excluded until the rolling average confirms."""
    return ~features["one_candle_wonder"].fillna(False)


def gate_no_gap_exclusion(features: pd.DataFrame, max_gap_pct: float = 0.20) -> pd.Series:
    """04 §3: a >20% open gap is excluded for the day (momentum-gapper
    regime, out of scope)."""
    return features["gap_pct"].abs() <= max_gap_pct


def gate_benchmark_crosscheck_long(features: pd.DataFrame, threshold: float = 1.0) -> pd.Series:
    """G9: only meaningful when features_m5.py was given a `qqq_m5` frame
    (producing `rolling_rrs_m5_qqq`); passes unconditionally otherwise."""
    col = features.get("rolling_rrs_m5_qqq")
    if col is None:
        return pd.Series(True, index=features.index)
    return col >= threshold


def gate_benchmark_crosscheck_short(features: pd.DataFrame, threshold: float = -1.0) -> pd.Series:
    col = features.get("rolling_rrs_m5_qqq")
    if col is None:
        return pd.Series(True, index=features.index)
    return col <= threshold


def gates_pass_long_m5(
    df: pd.DataFrame,
    features: pd.DataFrame,
    earnings_blackout: set | None = None,
    min_price: float = 10.0,
    min_adv_shares: float = 1_000_000,
    rrs_m5_threshold: float = 1.0,
    rrs_d1_threshold: float = 1.0,
    min_ha_days: int = 2,
    min_headroom_atr: float = 1.0,
    min_rvol: float = 1.0,
    max_gap_pct: float = 0.20,
    use_qqq_crosscheck: bool = False,
    disabled: frozenset = frozenset(),
    adv20: pd.Series | None = None,
) -> pd.Series:
    """Full 9-gate long-side check (G1-G9) at M5 cadence. `disabled` reuses
    HARD_RULE_NAMES plus "rrs_m5"/"vwap" for the M3.5-style ablation study
    when it's extended to M5 in M7."""
    result = gates_pass_long(
        df, features, earnings_blackout, min_price, min_adv_shares,
        rrs_d1_threshold, "rolling_rrs_d1", min_ha_days, min_headroom_atr, min_rvol, disabled,
        adv20=adv20,
    )
    if "rrs_m5" not in disabled:
        result &= gate_rrs_m5_long(features, rrs_m5_threshold)
    if "vwap" not in disabled:
        result &= gate_vwap_long(features)
    result &= gate_not_one_candle_wonder(features)
    result &= gate_no_gap_exclusion(features, max_gap_pct)
    if use_qqq_crosscheck:
        result &= gate_benchmark_crosscheck_long(features)
    return result


def gates_pass_short_m5(
    df: pd.DataFrame,
    features: pd.DataFrame,
    earnings_blackout: set | None = None,
    min_price: float = 10.0,
    min_adv_shares: float = 1_000_000,
    rrs_m5_threshold: float = -1.0,
    rrs_d1_threshold: float = -1.0,
    min_ha_days: int = 2,
    min_headroom_atr: float = 1.0,
    min_rvol: float = 1.0,
    max_gap_pct: float = 0.20,
    use_qqq_crosscheck: bool = False,
    disabled: frozenset = frozenset(),
    adv20: pd.Series | None = None,
) -> pd.Series:
    result = gates_pass_short(
        df, features, earnings_blackout, min_price, min_adv_shares,
        rrs_d1_threshold, "rolling_rrs_d1", min_ha_days, min_headroom_atr, min_rvol, disabled,
        adv20=adv20,
    )
    if "rrs_m5" not in disabled:
        result &= gate_rrs_m5_short(features, rrs_m5_threshold)
    if "vwap" not in disabled:
        result &= gate_vwap_short(features)
    result &= gate_not_one_candle_wonder(features)
    result &= gate_no_gap_exclusion(features, max_gap_pct)
    if use_qqq_crosscheck:
        result &= gate_benchmark_crosscheck_short(features)
    return result
