"""Wilder ATR. algo-spec/02-indicators-and-calculations.md §2.

TR(t)  = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
ATR(t) = (ATR(t-1)*(n-1) + TR(t)) / n

Seeded with a simple mean of the first `n` true ranges (Wilder's own
convention), then continued via an EWM with alpha=1/n -- mathematically
identical to the recursive formula for all bars after the seed.
"""
import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]  # no prior close on bar 0
    return tr


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder ATR with period `n`. First n-1 values are NaN (insufficient
    history to seed); index n-1 is the simple mean of TR[0:n]."""
    tr = true_range(df)
    result = pd.Series(np.nan, index=df.index, dtype=float)
    if len(tr) < n:
        return result
    seed = tr.iloc[:n].mean()
    result.iloc[n - 1] = seed
    if len(tr) > n:
        # splice: continue the recursion from the seed rather than restarting
        # ewm's own internal seed, by prepending the seed as bar 0 of the tail
        # computation and dropping it afterward.
        spliced = pd.concat([pd.Series([seed]), tr.iloc[n:]], ignore_index=True)
        spliced_ewm = spliced.ewm(alpha=1.0 / n, adjust=False).mean()
        result.iloc[n:] = spliced_ewm.iloc[1:].to_numpy()
    return result
