"""Full M5 market bias engine. algo-spec/03-market-bias-engine.md §3-6.

Runs on every closed M5 bar of SPY. Eight score components (§3's table),
EMA-3-bar smoothing, 2-bar hold hysteresis (bias/buckets.py, shared with the
D1 walking skeleton), trendline-breach timing trigger (bias/trigger.py,
same sharing), a `warmup` flag for the first-45-minutes observation window
(§4), and a `flip_flatten` signal for the bias-flip handling rule (§6) that
M6's position management will act on.

Deviation: §7's scheduled-event blackout is explicitly "bias keeps computing
throughout" -- it only gates new *entries*, which are M6/algo-layer concerns,
not engine outputs. Not implemented here.
"""
import numpy as np
import pandas as pd

from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL, apply_hysteresis
from rs_spy.bias.daily_context import daily_context_series
from rs_spy.bias.regime import TREND_DOWN, TREND_UP
from rs_spy.bias.trigger import compute_trendline_trigger
from rs_spy.data.resample import align_causal, align_daily_to_intraday
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.candle_structure import chop_ratio, stacked_count
from rs_spy.indicators.rvol import rvol as rvol_fn
from rs_spy.indicators.trendlines import down_trendline, up_trendline
from rs_spy.indicators.vwap import vwap as vwap_fn

EMA_SPAN = 3
WARMUP_CUTOFF = pd.Timedelta(hours=10, minutes=15)
CHOP_WINDOW = 12
VWAP_FLAT_PCT = 0.0003
FLIP_STACK_THRESHOLD = 3
FLIP_RVOL_THRESHOLD = 1.5


def _et_time_of_day(index: pd.DatetimeIndex) -> pd.Series:
    et = index.tz_convert("America/New_York")
    return pd.Series(et - et.normalize(), index=index)


def _close_label(m1_series: pd.Series) -> pd.Series:
    """Raw M1 bars are open-labeled (timestamp = interval start); M5/H1 bars
    built by data.resample.resample_ohlcv are close-labeled (timestamp =
    interval end). align_causal's "<=" contract silently assumes both sides
    use the same convention -- calling it directly on an M1-cadence series
    against an M5 index lets the M5 bar labeled e.g. 13:35 pick up the M1 bar
    ALSO labeled 13:35, which covers [13:35, 13:36) and has not closed yet: a
    one-minute lookahead leak. Shifting the M1 series' index forward by one
    minute (open-label -> close-label) before aligning fixes it. Found during
    Task 4's build (selection/features_m5.py) when the same VWAP-on-M1
    composition leaked one minute of future data; applies equally here."""
    shifted = m1_series.copy()
    shifted.index = shifted.index + pd.Timedelta(minutes=1)
    return shifted


def compute_raw_score(
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
) -> pd.DataFrame:
    """`qqq_m5` must share `spy_m5`'s index. Returns c1..c8, raw_score
    (clamped to [-100,100]), and `warmup`."""
    spy_vwap_m1 = _close_label(vwap_fn(spy_m1))
    spy_vwap = align_causal(spy_vwap_m1, spy_m5.index)
    vwap_diff_pct = (spy_m5["close"] - spy_vwap) / spy_vwap
    c1 = pd.Series(0.0, index=spy_m5.index)
    c1[vwap_diff_pct > VWAP_FLAT_PCT] = 20.0
    c1[vwap_diff_pct < -VWAP_FLAT_PCT] = -20.0

    rvol_m5 = rvol_fn(spy_m5)
    sc = stacked_count(spy_m5, volume_ratio=rvol_m5)
    cr = chop_ratio(spy_m5, window=CHOP_WINDOW)
    c2 = pd.Series(0.0, index=spy_m5.index)
    c2[sc >= 3] = 20.0
    c2[sc <= -3] = -20.0
    c2[cr >= 0.6] = 0.0

    session = spy_m5.index.normalize()
    session_high = spy_m5["high"].groupby(session).cummax()
    session_low = spy_m5["low"].groupby(session).cummin()
    day_rng = (session_high - session_low).replace(0, np.nan)
    frac = (spy_m5["close"] - session_low) / day_rng
    c3 = pd.Series(0.0, index=spy_m5.index)
    c3[frac >= 2.0 / 3.0] = 10.0
    c3[frac <= 1.0 / 3.0] = -10.0

    daily_ctx = daily_context_series(spy_d1)
    prior_high = align_daily_to_intraday(daily_ctx["d1_high"], spy_m5.index)
    prior_low = align_daily_to_intraday(daily_ctx["d1_low"], spy_m5.index)
    c4 = pd.Series(0.0, index=spy_m5.index)
    c4[spy_m5["close"] > prior_high] = 10.0
    c4[spy_m5["close"] < prior_low] = -10.0

    atr14 = atr_fn(spy_m5, n=14)
    down_tl = down_trendline(spy_m5)
    up_tl = up_trendline(spy_m5)
    c5 = pd.Series(0.0, index=spy_m5.index)
    c5[down_tl.notna() & (spy_m5["close"] > down_tl)] += 10.0
    c5[up_tl.notna() & (spy_m5["close"] < up_tl)] -= 10.0

    bar_pc = spy_m5["close"] - spy_m5["close"].shift(1)
    up_bar = bar_pc > 0
    down_bar = bar_pc < 0
    suspect_rally = align_daily_to_intraday(daily_ctx["suspect_rally"], spy_m5.index).fillna(False)
    c6 = pd.Series(0.0, index=spy_m5.index)
    c6[up_bar & (rvol_m5 >= 1.2)] = 10.0
    c6[down_bar & (rvol_m5 >= 1.2)] = -10.0
    c6[up_bar & suspect_rally.astype(bool) & (rvol_m5 < 0.8)] = -10.0

    regime = align_daily_to_intraday(daily_ctx["regime_d1"], spy_m5.index)
    c7 = pd.Series(0.0, index=spy_m5.index)
    c7[up_bar & (regime == TREND_UP)] = 10.0
    c7[down_bar & (regime == TREND_DOWN)] = 10.0
    c7[up_bar & (regime == TREND_DOWN)] = -10.0
    c7[down_bar & (regime == TREND_UP)] = -10.0

    qqq_vwap_m1 = _close_label(vwap_fn(qqq_m1))
    qqq_vwap = align_causal(qqq_vwap_m1, spy_m5.index)
    qqq_diff_pct = (qqq_m5["close"] - qqq_vwap) / qqq_vwap
    spy_above = vwap_diff_pct > 0
    qqq_above = qqq_diff_pct > 0
    c8 = pd.Series(0.0, index=spy_m5.index)
    c8[spy_above == qqq_above] = 10.0
    c8[spy_above != qqq_above] = -10.0

    out = pd.DataFrame({"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "c6": c6, "c7": c7, "c8": c8})
    out["raw_score"] = out.sum(axis=1).clip(-100.0, 100.0)
    out.loc[atr14.isna(), "raw_score"] = np.nan
    out["warmup"] = _et_time_of_day(spy_m5.index) < WARMUP_CUTOFF
    out["sc"] = sc
    out["rvol_m5"] = rvol_m5
    return out


def _flip_flatten(bias: pd.Series, sc: pd.Series, rvol_m5: pd.Series) -> pd.Series:
    prior_bull = bias.shift(1).isin([BULL, STRONG_BULL])
    prior_bear = bias.shift(1).isin([BEAR, STRONG_BEAR])
    now_bear = bias.isin([BEAR, STRONG_BEAR])
    now_bull = bias.isin([BULL, STRONG_BULL])
    flip_down = prior_bull & now_bear & (sc <= -FLIP_STACK_THRESHOLD) & (rvol_m5 >= FLIP_RVOL_THRESHOLD)
    flip_up = prior_bear & now_bull & (sc >= FLIP_STACK_THRESHOLD) & (rvol_m5 >= FLIP_RVOL_THRESHOLD)
    return (flip_down | flip_up).rename("flip_flatten")


def bias_series(
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
) -> pd.DataFrame:
    components = compute_raw_score(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    smoothed = components["raw_score"].ewm(span=EMA_SPAN, adjust=False).mean()
    smoothed[components["raw_score"].isna()] = np.nan
    bucket = apply_hysteresis(smoothed, hold_bars=2)
    trigger = compute_trendline_trigger(spy_m5, bucket, atr_period=14)
    flip = _flip_flatten(bucket, components["sc"], components["rvol_m5"])

    return pd.DataFrame(
        {
            "raw_score": components["raw_score"],
            "smoothed_score": smoothed,
            "bias": bucket,
            "trigger": trigger,
            "warmup": components["warmup"],
            "flip_flatten": flip,
        }
    )
