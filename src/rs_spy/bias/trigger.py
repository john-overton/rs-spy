"""Shared trendline-breach timing trigger. algo-spec/03-market-bias-engine.md §5.

Cadence-agnostic: fires the bar SPY's close breaches its own down/up
trendline (or, absent an active trendline, the first bar bias reads
STRONG_BULL/STRONG_BEAR -- "if SPY is very bullish you do not need
to wait"). Used at D1 cadence by bias/engine_d1.py and at M5 cadence by
bias/engine.py; only `price_df`'s bar size and `bias`'s hold-2-bars hysteresis
(bias/buckets.py) differ between the two callers.
"""
import pandas as pd

from rs_spy.bias.buckets import BEAR, BULL, NO_TRIGGER, LONG_TRIGGER, SHORT_TRIGGER, STRONG_BEAR, STRONG_BULL
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.trendlines import breach_down, breach_up, down_trendline, up_trendline


def compute_trendline_trigger(price_df: pd.DataFrame, bias: pd.Series, atr_period: int = 14) -> pd.Series:
    atr_series = atr_fn(price_df, n=atr_period)
    down_tl = down_trendline(price_df)
    up_tl = up_trendline(price_df)

    b_up = breach_up(price_df["close"], down_tl, atr_series)
    b_down = breach_down(price_df["close"], up_tl, atr_series)
    # shift(1).fillna(False) on a bool Series introduces NaN at the boundary
    # and upcasts to object dtype, silently turning `~` into deprecated
    # per-element Python int inversion -- shift(fill_value=False) avoids the
    # NaN entirely, keeping this a real bool Series (see engine_d1.py, where
    # this exact bug was found and fixed during M3.5).
    fresh_up = b_up & ~b_up.shift(1, fill_value=False)
    fresh_down = b_down & ~b_down.shift(1, fill_value=False)

    strong_bull_no_line = (bias == STRONG_BULL) & down_tl.isna() & (bias.shift(1) != STRONG_BULL)
    strong_bear_no_line = (bias == STRONG_BEAR) & up_tl.isna() & (bias.shift(1) != STRONG_BEAR)

    long_trigger = bias.isin([BULL, STRONG_BULL]) & (fresh_up | strong_bull_no_line)
    short_trigger = bias.isin([BEAR, STRONG_BEAR]) & (fresh_down | strong_bear_no_line)

    result = pd.Series(NO_TRIGGER, index=price_df.index, dtype=object)
    result[long_trigger] = LONG_TRIGGER
    result[short_trigger] = SHORT_TRIGGER
    return result.rename("trigger")
