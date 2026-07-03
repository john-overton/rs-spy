"""Per-symbol D1 feature computation shared by gates.py, scoring.py, and the
D1 backtest engine. Not a spec module on its own -- just the composition
point for indicators/*.py used by the D1 walking skeleton (M3).
"""
import pandas as pd

from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.candle_structure import volume_ratio_d1
from rs_spy.indicators.headroom import headroom_long as headroom_long_fn
from rs_spy.indicators.headroom import headroom_short as headroom_short_fn
from rs_spy.indicators.heikin_ashi import ha_continuation
from rs_spy.indicators.rrs import price_change, rolling_rrs, rrs
from rs_spy.indicators.sma_stack import sma_stack

RRS_D1_WINDOW = 5
ATR_D1_PERIODS = 14
CONSISTENCY_WINDOW = 10
VOLUME_LOOKBACK = 20
HEADROOM_STRENGTH = 5
HEADROOM_LOOKBACK = 60


def compute_symbol_features(
    df: pd.DataFrame, benchmark: pd.DataFrame, rrs_window: int = RRS_D1_WINDOW
) -> pd.DataFrame:
    """`df` and `benchmark` (SPY) must share the same index (trading-day
    calendar). Returns one feature column per D1-available signal used by
    the gates/scoring/watchlist modules. `rrs_window` is exposed (rather than
    hardcoded to RRS_D1_WINDOW) for the M3.5 RRS sensitivity sweep."""
    atr_d1 = atr_fn(df, n=ATR_D1_PERIODS)
    bench_atr_d1 = atr_fn(benchmark, n=ATR_D1_PERIODS)

    per_bar_rrs = rrs(df["close"], atr_d1, benchmark["close"], bench_atr_d1, window=rrs_window)
    rolling = rolling_rrs(per_bar_rrs, window=rrs_window)

    out = pd.DataFrame(index=df.index)
    out["atr_d1"] = atr_d1
    out["rrs_d1"] = per_bar_rrs
    out["rolling_rrs_d1"] = rolling
    out["rrs_d1_std"] = per_bar_rrs.rolling(CONSISTENCY_WINDOW).std()
    out["ha_cont_d1"] = ha_continuation(df, atr_d1)
    out["sma_stack"] = sma_stack(df)
    out["headroom_long"] = headroom_long_fn(
        df, atr_d1, strength=HEADROOM_STRENGTH, lookback=HEADROOM_LOOKBACK
    )
    out["headroom_short"] = headroom_short_fn(
        df, atr_d1, strength=HEADROOM_STRENGTH, lookback=HEADROOM_LOOKBACK
    )
    out["volume_ratio_d1"] = volume_ratio_d1(df, lookback=VOLUME_LOOKBACK)
    out["day_pc"] = price_change(df["close"], window=1)
    out["bench_day_pc"] = price_change(benchmark["close"], window=1)
    out["close"] = df["close"]
    out["volume"] = df["volume"]
    return out
