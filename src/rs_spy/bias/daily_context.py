"""Pre-open daily-context pass. algo-spec/03-market-bias-engine.md §2.

Computed once per D1 session from SPY's own D1 history; feeds three of the
M5 bias engine's 8 score components (regime agreement, prior-day levels, and
the breakout-audit's cap on the bull side). Every column here describes that
row's OWN close-of-day state -- it is NOT causally shifted for intraday use.
Callers (bias/engine.py) broadcast this onto M5 timestamps via
data.resample.align_daily_to_intraday(..., shift=1), so a session's own
intraday bars see yesterday's row, matching "pre-open pass" (today's own D1
bar isn't closed yet during today's session).

Breakout audit (§2.3): operationalized as "a fresh D1 down-trendline breach
(or, mirrored, up-trendline breakdown) within the trailing 3 sessions whose
candle_structure.follow_through() check does not confirm" -- follow_through
itself also returns False for a breakout too recent to have its own 3-session
follow-through window closed yet, so `suspect_rally`/`suspect_selloff` can
read True for 1-2 sessions after a genuinely good breakout before resolving
to False; the bias engine's EMA+hysteresis smoothing (bias/buckets.py) damps
that transient. This is a documented simplification of "tight ranges + light
volume + bearish drift", not a literal re-implementation of that language.
"""
import numpy as np
import pandas as pd

from rs_spy.bias.regime import regime_d1
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.indicators.candle_structure import follow_through, volume_ratio_d1
from rs_spy.indicators.sma_stack import smas
from rs_spy.indicators.trendlines import breach_down, breach_up, down_trendline, up_trendline

BREAKOUT_LOOKBACK_SESSIONS = 3


def _suspect_after_breakout(
    df: pd.DataFrame, fresh_breach: pd.Series, volume_ratio: pd.Series, lookback_sessions: int
) -> pd.Series:
    n = len(df)
    breach_idx = np.where(fresh_breach.to_numpy())[0]
    suspect = np.zeros(n, dtype=bool)
    for t in range(n):
        window_start = max(0, t - lookback_sessions + 1)
        recent = [i for i in breach_idx if window_start <= i <= t]
        if not recent:
            continue
        latest = recent[-1]
        if not follow_through(df, latest, volume_ratio, n_sessions=lookback_sessions):
            suspect[t] = True
    return pd.Series(suspect, index=df.index)


def daily_context_series(spy_d1: pd.DataFrame) -> pd.DataFrame:
    sma50 = smas(spy_d1, periods=(50,))["sma50"]
    regime = regime_d1(spy_d1["close"], sma50)

    atr14 = atr_fn(spy_d1, n=14)
    down_tl = down_trendline(spy_d1)
    up_tl = up_trendline(spy_d1)
    breach = breach_up(spy_d1["close"], down_tl, atr14)
    breakdown = breach_down(spy_d1["close"], up_tl, atr14)
    fresh_breach = breach & ~breach.shift(1, fill_value=False)
    fresh_breakdown = breakdown & ~breakdown.shift(1, fill_value=False)

    vol_ratio = volume_ratio_d1(spy_d1)
    suspect_rally = _suspect_after_breakout(spy_d1, fresh_breach, vol_ratio, BREAKOUT_LOOKBACK_SESSIONS)
    suspect_selloff = _suspect_after_breakout(spy_d1, fresh_breakdown, vol_ratio, BREAKOUT_LOOKBACK_SESSIONS)

    return pd.DataFrame(
        {
            "regime_d1": regime,
            "d1_high": spy_d1["high"],
            "d1_low": spy_d1["low"],
            "d1_close": spy_d1["close"],
            "suspect_rally": suspect_rally,
            "suspect_selloff": suspect_selloff,
        },
        index=spy_d1.index,
    )
