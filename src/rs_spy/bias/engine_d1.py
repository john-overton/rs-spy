"""D1 walking-skeleton market bias engine.

This is a genuine simplification of 03-market-bias-engine.md, NOT a faithful
daily-cadence subset (see algo-spec plan, Open Risk #4). Several of the 8
intraday score components (VWAP side, M5 candle structure, intraday
range-position) have no clean daily equivalent and are approximated. A good
backtest result on this engine validates the core RS/RW thesis on a swing
timeframe; it does NOT validate the real M5 system built in M5/M6.

The trendline-breach timing trigger (03 §5) IS implemented at D1 cadence
(see compute_trigger) -- it is the primary entry mechanism in 05/06 Path A
("A Simple Strategy": wait for SPY to breach its trendline, then buy the
already-identified RS list), with the per-symbol dip-arm state machine in
selection/watchlist.py as the secondary path (05 §3) for the rest of the
window between triggers. Without Path A, D1 RS opportunities that clear
every gate are common enough, but the specific raw-RRS zero-crossing that
arms Path B is not -- confirmed empirically while building this milestone.

Eight D1-analog components (each roughly +-10 to +-20, mirroring 03 §3's
weighting so hard/structural signals dominate over soft ones):

  1. SMA stack side (ABOVE_ALL/BELOW_ALL/MIXED)                    +-20
  2. D1 candle "stacked" conviction-day streak                     +-20
  3. Close position within the day's own H-L range                +-10
  4. Day-over-day price change, scaled by D1 ATR                   +-10
  5. D1 trendline breach state (close vs down/up trendline)        +-10
  6. Volume confirmation (RVOL_D1 >= 1.2 in the move's direction)  +-10
  7. Agreement with regime_d1 (bias/regime.py)                     +-10
  8. QQQ same-direction-day agreement                               +-10

Smoothed via a 3-day EMA, then mapped to bias buckets with 2-day hysteresis
on entry (immediate exit back to NEUTRAL), matching 03 §3's thresholds
(+-25 bull/bear, +-60 strong).
"""
import numpy as np
import pandas as pd

from rs_spy.bias.buckets import (  # noqa: F401
    BEAR,
    BEAR_TH,
    BULL,
    BULL_TH,
    LONG_TRIGGER,
    NEUTRAL,
    NO_TRIGGER,
    SHORT_TRIGGER,
    STRONG_BEAR,
    STRONG_BEAR_TH,
    STRONG_BULL,
    STRONG_BULL_TH,
    apply_hysteresis,
)
from rs_spy.bias.regime import TREND_DOWN, TREND_UP, regime_d1
from rs_spy.bias.trigger import compute_trendline_trigger
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.candle_structure import stacked_count, volume_ratio_d1
from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL, smas, sma_stack
from rs_spy.indicators.trendlines import down_trendline, up_trendline

HOLD_DAYS = 2
EMA_SPAN = 3


def compute_raw_score(spy: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    """Returns a DataFrame with the 8 component columns (c1..c8) and their
    sum ('raw_score'), aligned to spy.index. `qqq` must share spy's index."""
    stack = sma_stack(spy)
    c1 = pd.Series(0.0, index=spy.index)
    c1[stack == ABOVE_ALL] = 20.0
    c1[stack == BELOW_ALL] = -20.0

    sc = stacked_count(spy)
    c2 = (sc.clip(-3, 3) / 3.0) * 20.0

    rng = (spy["high"] - spy["low"]).replace(0, np.nan)
    frac = (spy["close"] - spy["low"]) / rng
    c3 = (frac - 0.5) * 20.0

    atr14 = atr_fn(spy, n=14)
    day_pc = spy["close"] - spy["close"].shift(1)
    c4 = np.sign(day_pc) * (day_pc.abs() / atr14).clip(upper=1.0) * 10.0

    down_tl = down_trendline(spy)
    up_tl = up_trendline(spy)
    c5 = pd.Series(0.0, index=spy.index)
    c5[down_tl.notna() & (spy["close"] > down_tl)] += 10.0
    c5[up_tl.notna() & (spy["close"] < up_tl)] -= 10.0

    rvol = volume_ratio_d1(spy)
    up_day = day_pc > 0
    down_day = day_pc < 0
    c6 = pd.Series(0.0, index=spy.index)
    c6[up_day & (rvol >= 1.2)] = 10.0
    c6[down_day & (rvol >= 1.2)] = -10.0

    sma50 = smas(spy, periods=(50,))["sma50"]
    regime = regime_d1(spy["close"], sma50)
    c7 = pd.Series(0.0, index=spy.index)
    c7[up_day & (regime == TREND_UP)] = 10.0
    c7[down_day & (regime == TREND_DOWN)] = 10.0
    c7[up_day & (regime == TREND_DOWN)] = -10.0
    c7[down_day & (regime == TREND_UP)] = -10.0

    qqq_pc = qqq["close"] - qqq["close"].shift(1)
    agree = np.sign(day_pc) == np.sign(qqq_pc)
    nonzero = (day_pc != 0) & (qqq_pc != 0)
    c8 = pd.Series(0.0, index=spy.index)
    c8[agree & nonzero] = 10.0
    c8[~agree & nonzero] = -10.0

    out = pd.DataFrame(
        {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "c6": c6, "c7": c7, "c8": c8}
    )
    out["raw_score"] = out.sum(axis=1)
    out["regime_d1"] = regime
    # component 4 depends on ATR; upstream NaN (warmup) should invalidate the
    # whole score rather than silently scoring a partial sum as if complete.
    out.loc[atr14.isna(), "raw_score"] = np.nan
    return out


def compute_trigger(spy: pd.DataFrame, bias: pd.Series) -> pd.Series:
    """03 §5 timing trigger, D1 cadence -- see bias/trigger.py for the shared
    implementation. This is the primary entry mechanism (05/06 Path A); the
    per-symbol watchlist dip-arm state machine (selection/watchlist.py) is
    the secondary path used the rest of the time between triggers."""
    return compute_trendline_trigger(spy, bias, atr_period=14)


def bias_series_d1(spy: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    """Full D1 bias pipeline: raw component score -> EMA smoothing ->
    hysteresis bucket. Returns raw_score, smoothed_score, regime_d1, bias."""
    components = compute_raw_score(spy, qqq)
    smoothed = components["raw_score"].ewm(span=EMA_SPAN, adjust=False).mean()
    smoothed[components["raw_score"].isna()] = np.nan
    bucket = apply_hysteresis(smoothed, hold_bars=HOLD_DAYS)
    trigger = compute_trigger(spy, bucket)
    return pd.DataFrame(
        {
            "raw_score": components["raw_score"],
            "smoothed_score": smoothed,
            "regime_d1": components["regime_d1"],
            "bias": bucket,
            "trigger": trigger,
        }
    )
