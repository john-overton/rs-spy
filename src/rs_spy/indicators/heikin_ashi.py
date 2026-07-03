"""Heikin-Ashi continuation. algo-spec/02-indicators-and-calculations.md §5.

HA_Close = (O+H+L+C)/4
HA_Open  = (prev HA_Open + prev HA_Close)/2, seeded as (O[0]+C[0])/2

A day is a "qualifying" bullish continuation day if HA_Close > HA_Open and the
candle has (near) no bottom wick (HA_Open ~= HA_Low, within tolerance
0.05*ATR). Bearish is the mirror (no top wick, HA_Open ~= HA_High).
ha_cont_d1 is the signed streak length of consecutive qualifying days in the
same direction; a non-qualifying day resets the streak to 0.
"""
import numpy as np
import pandas as pd


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Returns a DataFrame with columns ha_open, ha_close, ha_high, ha_low
    aligned to df.index."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0

    ha_close_vals = ha_close.to_numpy()
    ha_open_vals = np.empty(len(df), dtype=float)
    ha_open_vals[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open_vals[i] = (ha_open_vals[i - 1] + ha_close_vals[i - 1]) / 2.0
    ha_open = pd.Series(ha_open_vals, index=df.index)

    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame(
        {"ha_open": ha_open, "ha_close": ha_close, "ha_high": ha_high, "ha_low": ha_low},
        index=df.index,
    )


def ha_continuation(df: pd.DataFrame, atr: pd.Series, wick_tolerance_atr_mult: float = 0.05) -> pd.Series:
    """Signed consecutive-day continuation count (02 §5). `atr` should be the
    daily ATR (e.g. ATR-14) aligned to df.index."""
    ha = heikin_ashi(df)
    tolerance = wick_tolerance_atr_mult * atr

    bullish = (ha["ha_close"] > ha["ha_open"]) & ((ha["ha_open"] - ha["ha_low"]).abs() <= tolerance)
    bearish = (ha["ha_close"] < ha["ha_open"]) & ((ha["ha_high"] - ha["ha_open"]).abs() <= tolerance)

    day_type = np.where(bullish, 1, np.where(bearish, -1, 0))
    day_type = np.where(atr.isna().to_numpy(), 0, day_type)  # can't qualify without a valid tolerance

    streak = np.zeros(len(df), dtype=int)
    prev = 0
    for i, dt in enumerate(day_type):
        if dt == 0:
            cur = 0
        elif dt == 1:
            cur = prev + 1 if prev > 0 else 1
        else:
            cur = prev - 1 if prev < 0 else -1
        streak[i] = cur
        prev = cur
    return pd.Series(streak, index=df.index, name="ha_cont_d1")
