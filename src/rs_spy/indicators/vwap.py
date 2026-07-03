"""Session VWAP (M5/M1). algo-spec/02-indicators-and-calculations.md §3.

Anchored at session open (09:30 ET), resetting each trading day. **Callers
must pre-filter `df` to RTH-only bars** via `data.session.filter_rth()` --
Alpaca's minute feed includes pre/post-market bars (confirmed against real
cached data), and this function has no way to distinguish "first bar of the
session" from "first bar of the UTC calendar day" on its own. Once filtered
to RTH, grouping by the UTC calendar date of each bar's timestamp is a safe
proxy for grouping by trading session (RTH never crosses a UTC midnight
boundary), so no explicit ET conversion is needed here.
"""
import pandas as pd


def vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]
    session = df.index.normalize()

    cum_pv = pv.groupby(session).cumsum()
    cum_vol = df["volume"].groupby(session).cumsum()
    return (cum_pv / cum_vol).rename("vwap")
