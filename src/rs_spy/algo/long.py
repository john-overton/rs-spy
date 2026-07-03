"""Long-bias entry qualification + stateless exit-signal series. algo-spec/05.

Entry path A (05 §2, trigger day) and path B (05 §3, dip re-entry) both funnel
through selection.watchlist's state machine reaching ENTRY_EVAL (see
backtest/engine_m5.py, Task 6); this module supplies the bar-close reconfirmation
checks each path requires before an order is actually submitted
(confirm_trigger_entry_long for path A, dip_quality_pass_long for path B), plus the
position-management rule set (05 §4) as stateless per-bar boolean Series -- the
stateful pieces (does this position exist, what's its entry price/stop, has the
first-fired rule already closed it) live in the event loop, matching the style
selection/gates.py already uses for D1.

Dip quality (05 §3's "PASS if mixed overlapping candles, RVOL(pullback) < 1.0,
depth <= 1.5xATR below the local high, VWAP held") is a discretionary, prose
description with no precise formula -- translated here into the project's existing
indicator vocabulary, the same kind of disclosed translation as
bias/daily_context.py's suspect_rally breakout audit: "mixed overlapping candles"
-> candle_structure.chop_ratio over the pullback window >= MIXED_CHOP_MIN;
"RVOL(pullback) < 1.0" -> mean rvol_m5 over the window; "depth" -> the rolling high
over the window minus the current low, in ATR units; "VWAP held" -> close stayed
above vwap_m5 for the whole window. FAIL ("stacked red candles or heavy-volume
drop") maps to candle_structure.stacked_count reaching <= -STACK_FAIL_COUNT
anywhere in the window, which excludes the pass regardless of the other checks.
"""
import pandas as pd

from rs_spy.bias.buckets import BEAR, STRONG_BEAR
from rs_spy.indicators.candle_structure import chop_ratio, stacked_count

NOT_EXTENDED_ATR_MULT = 1.0
DIP_PULLBACK_WINDOW = 6  # M5 bars (~30 min) considered for the dip-quality read
DIP_DEPTH_ATR_MULT = 1.5
MIXED_CHOP_MIN = 0.4
STACK_FAIL_COUNT = 3
LRSI_STALL_LEVEL = 80.0
PROFIT_TARGET_ATR_MULT = 1.0
CHOP_PROFIT_TARGET_MULT = 0.75
TRAIL_TRIGGER_ATR_MULT = 1.5
TRAIL_STOP_ATR_MULT = 0.25


def not_extended_long(close: pd.Series, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    return (close - ema8) <= NOT_EXTENDED_ATR_MULT * atr_m5


def confirm_trigger_entry_long(features: pd.DataFrame, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    """05 §2's trigger-bar reconfirmation: RollingRRS_M5 >= 1.0 still true,
    above VWAP, not extended."""
    return (
        (features["rolling_rrs_m5"] >= 1.0)
        & (features["close"] > features["vwap_m5"])
        & not_extended_long(features["close"], ema8, atr_m5)
    )


def dip_quality_pass_long(df_m5: pd.DataFrame, features: pd.DataFrame, atr_m5: pd.Series) -> pd.Series:
    """05 §3's dip-quality reconfirmation (see module docstring for the
    prose->indicator translation).

    chop_ratio's rolling(window) is over candle_structure.overlap_ratio,
    which itself needs one prior bar (shift(1)) to produce a value. Like all
    rolling-window indicators in this codebase (ATR, SMA, stacked_count), a
    `window`-bar chop_ratio window requires window bars of real prior history
    before producing a non-NaN value. In production, df_m5 always has trading
    history before any pullback, so this resolves normally. Test fixtures with
    insufficient prior history should have extra leading bars added to provide
    proper warmup, not have the window decreased.
    """
    window = DIP_PULLBACK_WINDOW
    cr = chop_ratio(df_m5, window=window)
    sc = stacked_count(df_m5, volume_ratio=features["rvol_m5"])
    rvol_avg = features["rvol_m5"].rolling(window).mean()
    local_high = df_m5["high"].rolling(window).max()
    depth = (local_high - df_m5["low"]) / atr_m5
    vwap_held = (df_m5["close"] > features["vwap_m5"]).rolling(window).min().astype(bool)
    stacked_red_fail = sc.rolling(window).min() <= -STACK_FAIL_COUNT

    passes = (cr >= MIXED_CHOP_MIN) & (rvol_avg < 1.0) & (depth <= DIP_DEPTH_ATR_MULT) & vwap_held & ~stacked_red_fail
    return passes.fillna(False)


def rs_failure_long(rolling_rrs_m5: pd.Series) -> pd.Series:
    """05 §4.3: RollingRRS_M5 < 0 for 2 consecutive bars."""
    below = rolling_rrs_m5 < 0
    return below & below.shift(1, fill_value=False)


def vwap_loss_long(close: pd.Series, vwap_m5: pd.Series) -> pd.Series:
    """05 §4.4: 2 consecutive M5 closes below VWAP."""
    below = close < vwap_m5
    return below & below.shift(1, fill_value=False)


def momentum_stall_long(lrsi_m5: pd.Series) -> pd.Series:
    """05 §4.5: LRSI crosses down through 80."""
    return (lrsi_m5.shift(1) >= LRSI_STALL_LEVEL) & (lrsi_m5 < LRSI_STALL_LEVEL)


def market_flip_exit_long(bias: pd.Series, flip_flatten: pd.Series) -> pd.Series:
    """05 §4.2: bias -> BEAR/STRONG_BEAR with stacked-red/RVOL confirmation.
    bias/engine.py's flip_flatten already encodes that stack+RVOL confirmation
    symmetrically for both flip directions -- restrict to the down-flip here."""
    return flip_flatten & bias.isin([BEAR, STRONG_BEAR])
