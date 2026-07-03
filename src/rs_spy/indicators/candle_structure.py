"""D1 candle-structure metrics. algo-spec/02-indicators-and-calculations.md §10.

The spec's stacked/chop/follow-through definitions are written for M5 (RVOL,
12-bar chop window). This module is the D1 adaptation used by the M3 walking
skeleton: `volume_ratio_d1` (volume vs. its own 20-day average) stands in for
the full time-of-day-adjusted RVOL from `indicators/rvol.py` (M5, built in
M5 milestone), and the chop window defaults to 5 sessions rather than 12 bars.
This is a documented simplification, not a faithful D1 mirror of the M5 spec.
"""
import numpy as np
import pandas as pd


def volume_ratio_d1(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    avg_vol = df["volume"].shift(1).rolling(lookback).mean()
    return df["volume"] / avg_vol


def body_pct(df: pd.DataFrame) -> pd.Series:
    rng = df["high"] - df["low"]
    return (df["close"] - df["open"]).abs() / rng.replace(0, np.nan)


def stacked_count(
    df: pd.DataFrame,
    min_body_pct: float = 0.6,
    min_volume_ratio: float = 1.2,
    volume_lookback: int = 20,
    volume_ratio: pd.Series | None = None,
) -> pd.Series:
    """Signed count of consecutive same-direction "conviction" bars: same
    direction close, body >= min_body_pct of range, volume_ratio >= threshold.

    `volume_ratio`, if given, is used in place of this module's own
    volume_ratio_d1 -- e.g. the M5 bias engine (bias/engine.py) passes the
    time-of-day-adjusted RVOL from indicators/rvol.py, per this module's
    docstring ("stands in for the full ... RVOL ... (M5, built in M5
    milestone)"). D1 callers (bias/engine_d1.py, backtest/engine.py) omit it
    and keep the original volume_ratio_d1 behavior."""
    direction = (df["close"] > df["open"]).astype(int) - (df["close"] < df["open"]).astype(int)
    vol_ratio = volume_ratio if volume_ratio is not None else volume_ratio_d1(df, lookback=volume_lookback)
    qualifies = (body_pct(df) >= min_body_pct) & (vol_ratio >= min_volume_ratio)
    signed = direction.where(qualifies, 0)

    streak = []
    prev = 0
    for val in signed.to_numpy():
        if val == 0:
            cur = 0
        elif val > 0:
            cur = prev + 1 if prev > 0 else 1
        else:
            cur = prev - 1 if prev < 0 else -1
        streak.append(cur)
        prev = cur
    return pd.Series(streak, index=df.index, name="stacked_count")


def overlap_ratio(df: pd.DataFrame) -> pd.Series:
    """Fraction of today's range that overlaps yesterday's range."""
    high, low = df["high"], df["low"]
    prev_high, prev_low = high.shift(1), low.shift(1)
    intersection = (
        pd.concat([high, prev_high], axis=1).min(axis=1, skipna=False)
        - pd.concat([low, prev_low], axis=1).max(axis=1, skipna=False)
    ).clip(lower=0)
    rng = (high - low).replace(0, np.nan)
    return intersection / rng


def chop_ratio(df: pd.DataFrame, window: int = 5) -> pd.Series:
    return overlap_ratio(df).rolling(window).mean()


def follow_through(
    df: pd.DataFrame,
    breakout_idx: int,
    volume_ratio: pd.Series,
    n_sessions: int = 3,
    min_volume_ratio: float = 1.0,
) -> bool:
    """03 §2.3 real-vs-fake breakout audit: did the `n_sessions` after
    `breakout_idx` close above the breakout candle's midpoint on adequate
    volume? Returns False if there isn't enough subsequent history yet."""
    if breakout_idx + n_sessions >= len(df):
        return False
    midpoint = (df["open"].iloc[breakout_idx] + df["close"].iloc[breakout_idx]) / 2.0
    window = df.iloc[breakout_idx + 1 : breakout_idx + 1 + n_sessions]
    vol_window = volume_ratio.iloc[breakout_idx + 1 : breakout_idx + 1 + n_sessions]
    closes_hold = (window["close"] > midpoint).all()
    volume_ok = (vol_window >= min_volume_ratio).any()
    return bool(closes_hold and volume_ok)
