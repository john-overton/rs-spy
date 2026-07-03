"""Algorithmic trendlines. algo-spec/02-indicators-and-calculations.md §9.

Pivot *detection* is vectorized (indicators/headroom.py pivot_highs/pivot_lows
-- shared implementation, since both headroom and trendlines are built on the
same swing-pivot primitive). Trendline *construction/refit* loops over the
(much smaller) set of confirmed pivots, per the spec's own stated exception.

A down-trendline connects the two most recent confirmed pivot highs (>=
min_gap bars apart), refit whenever a new one is confirmed; up-trendline
mirrors on pivot lows. Breach = a bar's close crosses the line by more than
`tolerance_mult * atr` (close-through, not wick-through, per spec).
"""
import numpy as np
import pandas as pd

from rs_spy.indicators.headroom import pivot_highs, pivot_lows


def _fit_line(df: pd.DataFrame, pivot_mask: pd.Series, price_col: str, strength: int, min_gap: int) -> pd.Series:
    """line_value(t): the trendline's price at bar t, extrapolated from the
    two most recent pivots confirmed as of t (>= min_gap apart). NaN until
    two such pivots exist."""
    price = df[price_col].to_numpy()
    confirmed_idx: list[int] = []
    out = np.full(len(df), np.nan)

    pivot_at = np.where(pivot_mask.to_numpy())[0]
    pivot_ptr = 0

    p1 = p2 = None  # (index, price) of the two most recent qualifying pivots
    for t in range(len(df)):
        # confirm any pivots that have become knowable as of bar t
        while pivot_ptr < len(pivot_at) and pivot_at[pivot_ptr] + strength <= t:
            j = pivot_at[pivot_ptr]
            confirmed_idx.append(j)
            pivot_ptr += 1
            if p2 is None or j - p2[0] >= min_gap:
                p1, p2 = p2, (j, price[j])
            else:
                # too close to the last pivot to form a fresh leg; replace p2
                p2 = (j, price[j])

        if p1 is not None and p2 is not None:
            slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
            out[t] = p2[1] + slope * (t - p2[0])
    return pd.Series(out, index=df.index)


def down_trendline(df: pd.DataFrame, strength: int = 3, min_gap: int = 6) -> pd.Series:
    return _fit_line(df, pivot_highs(df, strength), "high", strength, min_gap).rename("down_trendline")


def up_trendline(df: pd.DataFrame, strength: int = 3, min_gap: int = 6) -> pd.Series:
    return _fit_line(df, pivot_lows(df, strength), "low", strength, min_gap).rename("up_trendline")


def breach_up(close: pd.Series, line_value: pd.Series, atr: pd.Series, tolerance_mult: float = 0.05) -> pd.Series:
    """Close breaches a down-trendline upward (long trigger)."""
    return close > (line_value + tolerance_mult * atr)


def breach_down(close: pd.Series, line_value: pd.Series, atr: pd.Series, tolerance_mult: float = 0.05) -> pd.Series:
    """Close breaches an up-trendline downward (short trigger)."""
    return close < (line_value - tolerance_mult * atr)
