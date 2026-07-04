"""M7.5 Phase 0 (tuning-matrix cell D1): trigger forward-return study.

The M7 bias confusion matrix (bias_confusion_m5.py) tested the bias BUCKET
and found ~zero directional skill above base rate -- but the signal that
actually gates 100% of the real backtest's entries is the trendline-breach
TRIGGER (bias_df["trigger"]), which had never been tested in isolation. This
study is the analog of real-life practice timing SPY entries off a
market-timing oscillator signal (OneOption's "1OP cross"): for every
LONG_TRIGGER / SHORT_TRIGGER fire, classify SPY's own forward return over
each horizon as UP/FLAT/DOWN and compare against the all-bars base rate.
Fires have real sample sizes (~1,591 long / ~561 short over 5 years), unlike
the 3-trade backtest sample. Needs no backtest run -- only the bias engine's
own output and SPY's M5 close series. Returns are measured from the fire
bar's close; the live engine enters via a next-bar limit order -- read these
numbers as signal skill vs. base rate, not achievable PnL.
"""
import pandas as pd

from rs_spy.bias.buckets import LONG_TRIGGER, SHORT_TRIGGER
from rs_spy.bias.engine import bias_series

DEFAULT_HORIZONS = (6, 12, 24)  # 30 min / 1 h / 2 h at M5 cadence
DEFAULT_FLAT_THRESHOLD_PCT = 0.001  # same flat band as bias_confusion_m5.py


def trigger_skill_table(
    trigger: pd.Series,
    close: pd.Series,
    horizons: tuple = DEFAULT_HORIZONS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> pd.DataFrame:
    """One row per (horizon, signal) for signal in ALL / LONG_TRIGGER /
    SHORT_TRIGGER. Bars whose forward return is undefined (fewer than
    `horizon` bars of subsequent history) are excluded from `n`. The ALL row
    is the base rate every trigger row must beat to claim any skill."""
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for label, mask in (
            ("ALL", pd.Series(True, index=trigger.index)),
            (LONG_TRIGGER, trigger == LONG_TRIGGER),
            (SHORT_TRIGGER, trigger == SHORT_TRIGGER),
        ):
            sub = fwd[mask & fwd.notna()]
            n = len(sub)
            rows.append(
                {
                    "horizon_bars": horizon,
                    "signal": label,
                    "n": n,
                    "pct_up": float((sub > flat_threshold_pct).mean()) if n else None,
                    "pct_flat": float((sub.abs() <= flat_threshold_pct).mean()) if n else None,
                    "pct_down": float((sub < -flat_threshold_pct).mean()) if n else None,
                    "mean_fwd_return": float(sub.mean()) if n else None,
                    "median_fwd_return": float(sub.median()) if n else None,
                }
            )
    return pd.DataFrame(rows, dtype=object)


def run_trigger_skill_m5(
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
    horizons: tuple = DEFAULT_HORIZONS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> pd.DataFrame:
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    return trigger_skill_table(bias_df["trigger"], spy_m5["close"], horizons, flat_threshold_pct)
