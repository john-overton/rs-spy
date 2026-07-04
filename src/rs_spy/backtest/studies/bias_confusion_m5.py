"""M7 bias-engine confusion matrix. algo-spec/08-backtesting-and-validation.md
§3.4. Not yet built at any cadence (M3.5 covered §3.1-3.3 only).

For every M5 bar with a resolved bias bucket (bias/engine.py's
BULL/STRONG_BULL/NEUTRAL/BEAR/STRONG_BEAR), classifies SPY's own forward
realized price direction over the following `horizon_bars` M5 bars as UP,
DOWN, or FLAT (a return within +-`flat_threshold_pct` of zero), and builds
a bucket x realized-direction contingency table plus a directional hit
rate (BULL/STRONG_BULL bars where realized was UP; BEAR/STRONG_BEAR bars
where realized was DOWN; NEUTRAL bars where realized was FLAT) -- the
natural cadence-agnostic way to ask "is the bias engine's call actually
predictive of what SPY does next." Needs no backtest run -- only the bias
engine's own output and SPY's M5 close series.
"""
import pandas as pd

from rs_spy.bias.buckets import BEAR, BULL, NEUTRAL, STRONG_BEAR, STRONG_BULL
from rs_spy.bias.engine import bias_series

UP = "UP"
DOWN = "DOWN"
FLAT = "FLAT"

DEFAULT_HORIZON_BARS = 12  # ~1 hour at M5 cadence
DEFAULT_FLAT_THRESHOLD_PCT = 0.001  # 0.1%


def _forward_direction(close: pd.Series, horizon_bars: int, flat_threshold_pct: float) -> pd.Series:
    forward_return = close.shift(-horizon_bars) / close - 1.0
    direction = pd.Series(FLAT, index=close.index, dtype=object)
    direction[forward_return > flat_threshold_pct] = UP
    direction[forward_return < -flat_threshold_pct] = DOWN
    direction[forward_return.isna()] = None
    return direction


def run_bias_confusion_m5(
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> dict:
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    direction = _forward_direction(spy_m5["close"], horizon_bars, flat_threshold_pct)

    df = pd.DataFrame({"bias": bias_df["bias"], "realized": direction}).dropna()
    bucket_order = [STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR]
    direction_order = [UP, FLAT, DOWN]

    contingency = (
        pd.crosstab(df["bias"], df["realized"])
        .reindex(index=bucket_order, columns=direction_order, fill_value=0)
        .reset_index()
    )

    hit_rates = {}
    for bucket in (STRONG_BULL, BULL):
        sub = df[df["bias"] == bucket]
        hit_rates[bucket] = float((sub["realized"] == UP).mean()) if not sub.empty else None
    for bucket in (STRONG_BEAR, BEAR):
        sub = df[df["bias"] == bucket]
        hit_rates[bucket] = float((sub["realized"] == DOWN).mean()) if not sub.empty else None
    sub = df[df["bias"] == NEUTRAL]
    hit_rates[NEUTRAL] = float((sub["realized"] == FLAT).mean()) if not sub.empty else None

    return {"contingency": contingency, "hit_rates": hit_rates, "n_bars": len(df)}
