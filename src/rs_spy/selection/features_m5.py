"""Per-symbol M5-cadence feature computation. algo-spec/02, 04 §2-4.

Mirrors selection/features.py's role at D1 cadence: the composition point
gates_m5/scoring_m5/watchlist read from, not a spec module on its own. Reuses
selection/features.py's D1 computation directly for the D1-only signals
(ha_cont_d1, sma_stack, headroom, D1 RRS) rather than re-deriving them, then
broadcasts those D1 values onto the M5 index causally (yesterday's D1 row,
per data.resample.align_daily_to_intraday's contract) since gates_m5/G4-G6
need "as of this M5 bar" access to D1-cadence signals.

RRS_M5 (02 §1.1) uses ATR50 on H1 bars, resampled from the symbol's own M5
bars and the SPY M5 bars, then causally aligned back onto the M5 index (see
data/resample.py's module docstring for why). VWAP and RVOL are computed on
the raw 1-minute frame (02 §3's "from 1-min bars") and causally aligned onto
the M5 index. LRSI runs directly on M5 closes (its gamma=0.5 default is
calibrated for 5-minute spacing).

Raw M1 bars are *open*-labeled (a bar timestamped 09:30 covers [09:30,
09:31) -- see data/session.py's RTH mask, which treats 09:30 as the first
valid RTH minute), whereas resample_ohlcv's M5/H1 output is *close*-labeled
(a bar timestamped 13:35 covers [13:30, 13:35)). align_causal's "most recent
source at or before target" contract assumes both sides share one
convention, so aligning M1-cadence VWAP/RVOL straight onto the M5 index
would pick up the M1 bar that *starts* exactly at the M5 bar's own
timestamp -- one minute of data that bar hasn't closed on yet. `_close_label`
below shifts an M1-cadence series' index forward by one minute (open-label
-> close-label) before every such alignment to prevent that leak.

One-candle-wonder anti-pattern exclusion (04 §3): a single M5 bar
contributing >60% of the RRS window's total |price change| -- computed here,
consumed as a gate in selection/gates.py.
"""
import numpy as np
import pandas as pd

from rs_spy.bias.daily_context import daily_context_series
from rs_spy.data.resample import align_causal, align_daily_to_intraday, resample_ohlcv
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.laguerre_rsi import laguerre_rsi
from rs_spy.indicators.rrs import power_index, rolling_rrs, rrs
from rs_spy.indicators.rvol import rvol as rvol_fn
from rs_spy.indicators.vwap import vwap as vwap_fn
from rs_spy.selection.features import compute_symbol_features

RRS_M5_WINDOW = 12
H1_ATR_PERIOD = 50
ONE_CANDLE_WONDER_FRACTION = 0.6


def _close_label(m1_series: pd.Series) -> pd.Series:
    """Convert an M1-cadence series from open-label (timestamp = bar start)
    to close-label (timestamp = bar end) by shifting its index forward one
    minute, so it can be safely align_causal'd against a close-labeled M5/H1
    index without leaking the next minute bar (see module docstring)."""
    shifted = m1_series.copy()
    shifted.index = shifted.index + pd.Timedelta(minutes=1)
    return shifted


def _h1_atr_aligned(df_m5: pd.DataFrame, h1_atr_period: int, target_index: pd.DatetimeIndex) -> pd.Series:
    h1 = resample_ohlcv(df_m5, "1h")
    h1_atr = atr_fn(h1, n=h1_atr_period)
    return align_causal(h1_atr, target_index)


def _one_candle_wonder(close: pd.Series, window: int, fraction: float) -> pd.Series:
    bar_change = close.diff().abs()
    window_change = close.diff(window).abs()
    dominant = bar_change.rolling(window).max()
    frac = (dominant / window_change.replace(0, np.nan)).fillna(0.0)
    return frac > fraction


def compute_symbol_features_m5(
    df_m1: pd.DataFrame,
    df_m5: pd.DataFrame,
    df_d1: pd.DataFrame,
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m5: pd.DataFrame | None = None,
    rrs_window: int = RRS_M5_WINDOW,
    h1_atr_period: int = H1_ATR_PERIOD,
) -> pd.DataFrame:
    stock_h1_atr = _h1_atr_aligned(df_m5, h1_atr_period, df_m5.index)
    spy_h1_atr = _h1_atr_aligned(spy_m5, h1_atr_period, df_m5.index)
    spy_close_aligned = spy_m5["close"].reindex(df_m5.index)

    per_bar_rrs = rrs(df_m5["close"], stock_h1_atr, spy_close_aligned, spy_h1_atr, window=rrs_window)
    rolling = rolling_rrs(per_bar_rrs, window=rrs_window)
    pi = power_index(spy_close_aligned, spy_h1_atr, window=rrs_window)

    vwap_m1 = _close_label(vwap_fn(df_m1))
    vwap_m5 = align_causal(vwap_m1, df_m5.index)
    rvol_m1 = _close_label(rvol_fn(df_m1))
    rvol_m5 = align_causal(rvol_m1, df_m5.index)
    lrsi_m5 = laguerre_rsi(df_m5["close"])

    d1_feat = compute_symbol_features(df_d1, spy_d1)
    d1_aligned = pd.DataFrame(
        {col: align_daily_to_intraday(d1_feat[col], df_m5.index) for col in d1_feat.columns}
    )

    daily_ctx = daily_context_series(spy_d1)
    prior_close = align_daily_to_intraday(daily_ctx["d1_close"], df_m5.index)
    session = df_m5.index.normalize()
    session_open = df_m5["open"].groupby(session).transform("first")
    gap_pct = (session_open - prior_close) / prior_close

    out = pd.DataFrame(index=df_m5.index)
    out["rrs_m5"] = per_bar_rrs
    out["rolling_rrs_m5"] = rolling
    out["power_index_m5"] = pi
    out["vwap_m5"] = vwap_m5
    out["rvol_m5"] = rvol_m5
    out["lrsi_m5"] = lrsi_m5
    out["one_candle_wonder"] = _one_candle_wonder(df_m5["close"], rrs_window, ONE_CANDLE_WONDER_FRACTION)
    out["gap_pct"] = gap_pct
    out["close"] = df_m5["close"]
    out["volume"] = df_m5["volume"]

    for col in ["rrs_d1", "rolling_rrs_d1", "ha_cont_d1", "sma_stack", "headroom_long", "headroom_short",
                "volume_ratio_d1"]:
        out[col] = d1_aligned[col]

    if qqq_m5 is not None:
        qqq_h1_atr = _h1_atr_aligned(qqq_m5, h1_atr_period, df_m5.index)
        qqq_close_aligned = qqq_m5["close"].reindex(df_m5.index)
        per_bar_rrs_qqq = rrs(df_m5["close"], stock_h1_atr, qqq_close_aligned, qqq_h1_atr, window=rrs_window)
        out["rrs_m5_qqq"] = per_bar_rrs_qqq
        out["rolling_rrs_m5_qqq"] = rolling_rrs(per_bar_rrs_qqq, window=rrs_window)

    return out
