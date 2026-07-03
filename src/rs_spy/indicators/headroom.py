"""Headroom to resistance/support, D1. algo-spec/02-indicators-and-calculations.md §7.

Resistance/support candidates: major D1 SMAs, confirmed swing pivots, and the
52-week high/low. A pivot high at index j (a centered local max over a
window of `2*strength+1` bars) is only usable as a resistance candidate once
it is *confirmed* -- i.e. once bars up through j+strength exist -- so that
headroom(t) never depends on bars after t (see the no-lookahead test class
in tests/unit/test_no_lookahead.py). The nearest-candidate search across
pivots is a plain loop over `lookback` sessions per bar (cheap at D1 cadence,
same category of exception as trendline construction in 02 §9).
"""
import numpy as np
import pandas as pd

from rs_spy.indicators.sma_stack import PERIODS, smas


def pivot_highs(df: pd.DataFrame, strength: int = 5) -> pd.Series:
    window = 2 * strength + 1
    centered_max = df["high"].rolling(window, center=True, min_periods=window).max()
    return (df["high"] == centered_max) & centered_max.notna()


def pivot_lows(df: pd.DataFrame, strength: int = 5) -> pd.Series:
    window = 2 * strength + 1
    centered_min = df["low"].rolling(window, center=True, min_periods=window).min()
    return (df["low"] == centered_min) & centered_min.notna()


def _nearest_candidate(candidates: list[float], close: float, above: bool) -> float | None:
    if above:
        filtered = [c for c in candidates if c > close]
        return min(filtered) if filtered else None
    filtered = [c for c in candidates if c < close]
    return max(filtered) if filtered else None


def headroom_long(
    df: pd.DataFrame,
    atr_d1: pd.Series,
    strength: int = 5,
    lookback: int = 60,
) -> pd.Series:
    """(nearest_resistance - close) / atr_d1. NaN (treated as "infinite
    headroom", full score) if no resistance candidate is found above price."""
    sma_df = smas(df, PERIODS)
    pivots = pivot_highs(df, strength)
    pivot_price = df["high"].where(pivots)
    week52_high = df["high"].rolling(252, min_periods=1).max()

    close = df["close"].to_numpy()
    atr = atr_d1.to_numpy()
    out = np.full(len(df), np.nan)

    for t in range(len(df)):
        candidates: list[float] = [week52_high.iat[t]]
        for p in PERIODS:
            v = sma_df[f"sma{p}"].iat[t]
            if not pd.isna(v):
                candidates.append(v)
        lo = max(0, t - lookback)
        hi = t - strength  # only pivots confirmed as of bar t
        if hi >= lo:
            for j in range(lo, hi + 1):
                v = pivot_price.iat[j]
                if not pd.isna(v):
                    candidates.append(v)
        nearest = _nearest_candidate(candidates, close[t], above=True)
        if nearest is not None and not pd.isna(atr[t]) and atr[t] > 0:
            out[t] = (nearest - close[t]) / atr[t]
    return pd.Series(out, index=df.index, name="headroom_long")


def headroom_short(
    df: pd.DataFrame,
    atr_d1: pd.Series,
    strength: int = 5,
    lookback: int = 60,
) -> pd.Series:
    """(close - nearest_support) / atr_d1, mirroring headroom_long."""
    sma_df = smas(df, PERIODS)
    pivots = pivot_lows(df, strength)
    pivot_price = df["low"].where(pivots)
    week52_low = df["low"].rolling(252, min_periods=1).min()

    close = df["close"].to_numpy()
    atr = atr_d1.to_numpy()
    out = np.full(len(df), np.nan)

    for t in range(len(df)):
        candidates: list[float] = [week52_low.iat[t]]
        for p in PERIODS:
            v = sma_df[f"sma{p}"].iat[t]
            if not pd.isna(v):
                candidates.append(v)
        lo = max(0, t - lookback)
        hi = t - strength
        if hi >= lo:
            for j in range(lo, hi + 1):
                v = pivot_price.iat[j]
                if not pd.isna(v):
                    candidates.append(v)
        nearest = _nearest_candidate(candidates, close[t], above=False)
        if nearest is not None and not pd.isna(atr[t]) and atr[t] > 0:
            out[t] = (close[t] - nearest) / atr[t]
    return pd.Series(out, index=df.index, name="headroom_short")
