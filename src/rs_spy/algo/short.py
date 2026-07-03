"""Short-bias entry qualification + stateless exit-signal series. algo-spec/06.

Mirror of algo/long.py -- see that module's docstring for the dip/bounce-quality
translation methodology (identical here, mirrored to the downside). Two real
asymmetries vs. the long side, both directly from spec text, not omissions:
market_flip_exit_short is UNCONDITIONAL (06 §4: "exit all shorts at market -- not
merely tightened -- asymmetric vs the long side because upside squeezes are
faster"), unlike the long side's stacked/RVOL-confirmed flip; and there's an
additional squeeze_guard_short with no long-side equivalent (06 §4: "any M5 bar
against the position >= 2.0xATR on RVOL >= 2.0 -> exit immediately regardless of
RRS").
"""
import pandas as pd

from rs_spy.bias.buckets import BULL, STRONG_BULL
from rs_spy.indicators.candle_structure import chop_ratio, stacked_count

NOT_EXTENDED_ATR_MULT = 1.0
DIP_PULLBACK_WINDOW = 6  # M5 bars (~30 min) considered for the bounce-quality read
DIP_DEPTH_ATR_MULT = 1.5
MIXED_CHOP_MIN = 0.4
STACK_FAIL_COUNT = 3
LRSI_STALL_LEVEL = 20.0
PROFIT_TARGET_ATR_MULT = 1.0
CHOP_PROFIT_TARGET_MULT = 0.75
TRAIL_TRIGGER_ATR_MULT = 1.5
TRAIL_STOP_ATR_MULT = 0.25
SQUEEZE_ATR_MULT = 2.0
SQUEEZE_RVOL_MULT = 2.0


def not_extended_short(close: pd.Series, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    return (ema8 - close) <= NOT_EXTENDED_ATR_MULT * atr_m5


def confirm_trigger_entry_short(features: pd.DataFrame, ema8: pd.Series, atr_m5: pd.Series) -> pd.Series:
    """06 §2's trigger-bar reconfirmation: RollingRRS_M5 <= -1.0 still true,
    below VWAP, not extended."""
    return (
        (features["rolling_rrs_m5"] <= -1.0)
        & (features["close"] < features["vwap_m5"])
        & not_extended_short(features["close"], ema8, atr_m5)
    )


def bounce_quality_pass_short(df_m5: pd.DataFrame, features: pd.DataFrame, atr_m5: pd.Series) -> pd.Series:
    """06 §3's bounce-quality reconfirmation (see algo/long.py's module
    docstring for the prose->indicator translation methodology; mirrored here
    to the upside bounce within a downtrend).

    chop_ratio's rolling(window) is over candle_structure.overlap_ratio, which
    itself needs one prior bar (shift(1)) to produce a value. Like all
    rolling-window indicators in this codebase (ATR, SMA, stacked_count), a
    `window`-bar chop_ratio window requires window bars of real prior history
    before producing a non-NaN value. In production, df_m5 always has trading
    history before any bounce, so this resolves normally. Test fixtures with
    insufficient prior history should have extra leading bars added to provide
    proper warmup, not have the window decreased.
    """
    window = DIP_PULLBACK_WINDOW
    cr = chop_ratio(df_m5, window=window)
    sc = stacked_count(df_m5, volume_ratio=features["rvol_m5"])
    rvol_avg = features["rvol_m5"].rolling(window).mean()
    local_low = df_m5["low"].rolling(window).min()
    depth = (df_m5["high"] - local_low) / atr_m5
    vwap_held = (df_m5["close"] < features["vwap_m5"]).rolling(window).min().astype(bool)
    stacked_green_fail = sc.rolling(window).max() >= STACK_FAIL_COUNT

    passes = (cr >= MIXED_CHOP_MIN) & (rvol_avg < 1.0) & (depth <= DIP_DEPTH_ATR_MULT) & vwap_held & ~stacked_green_fail
    return passes.fillna(False)


def rs_failure_short(rolling_rrs_m5: pd.Series) -> pd.Series:
    """06 §4: RollingRRS_M5 > 0 for 2 consecutive bars."""
    above = rolling_rrs_m5 > 0
    return above & above.shift(1, fill_value=False)


def vwap_loss_short(close: pd.Series, vwap_m5: pd.Series) -> pd.Series:
    """06 §4: 2 consecutive M5 closes above VWAP."""
    above = close > vwap_m5
    return above & above.shift(1, fill_value=False)


def momentum_stall_short(lrsi_m5: pd.Series) -> pd.Series:
    """06 §4: LRSI crosses up through 20."""
    return (lrsi_m5.shift(1) <= LRSI_STALL_LEVEL) & (lrsi_m5 > LRSI_STALL_LEVEL)


def market_flip_exit_short(bias: pd.Series) -> pd.Series:
    """06 §4: bias -> BULL/STRONG_BULL -- exit all shorts at market,
    unconditional (no stacked/RVOL confirmation), unlike the long side's
    market_flip_exit_long. Asymmetric because upside squeezes are faster."""
    return bias.isin([BULL, STRONG_BULL])


def squeeze_guard_short(
    bar_high: pd.Series, prev_close: pd.Series, atr_m5: pd.Series, rvol_m5: pd.Series
) -> pd.Series:
    """06 §4: any M5 bar against the position >= 2.0xATR on RVOL >= 2.0 ->
    exit immediately regardless of RRS. No long-side equivalent."""
    adverse_move = bar_high - prev_close
    return (adverse_move >= SQUEEZE_ATR_MULT * atr_m5) & (rvol_m5 >= SQUEEZE_RVOL_MULT)
