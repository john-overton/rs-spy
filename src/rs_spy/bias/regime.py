"""D1 regime classifier. algo-spec/03-market-bias-engine.md §2.1.

"20-day linear-regression slope of closes, sign-checked against SMA50 slope
... Both agree up -> TREND_UP; both down -> TREND_DOWN; else CHOP."
"""
import numpy as np
import pandas as pd

TREND_UP = "TREND_UP"
CHOP = "CHOP"
TREND_DOWN = "TREND_DOWN"


def linreg_slope(series: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_centered = x - x_mean
    x_var = (x_centered**2).sum()

    def _slope(y: np.ndarray) -> float:
        return float((x_centered * (y - y.mean())).sum() / x_var)

    return series.rolling(window).apply(_slope, raw=True)


def regime_d1(
    close: pd.Series,
    sma50: pd.Series,
    slope_window: int = 20,
    sma_slope_window: int = 5,
) -> pd.Series:
    close_slope = linreg_slope(close, slope_window)
    sma_slope = sma50.diff(sma_slope_window)

    up = (close_slope > 0) & (sma_slope > 0)
    down = (close_slope < 0) & (sma_slope < 0)
    has_nan = close_slope.isna() | sma_slope.isna()

    result = pd.Series(CHOP, index=close.index, dtype=object)
    result[up] = TREND_UP
    result[down] = TREND_DOWN
    result[has_nan] = None
    return result.rename("regime_d1")
