"""Real Relative Strength (RRS). algo-spec/02-indicators-and-calculations.md §1.

PC_S(t)         = Close_S(t) - Close_S(t-L)
PC_M(t)         = Close_M(t) - Close_M(t-L)
PowerIndex(t)   = PC_M(t) / ATR_M           ("SPY Power Index")
ExpectedPC_S(t) = PowerIndex(t) * ATR_S
RRS(t)          = (PC_S(t) - ExpectedPC_S(t)) / ATR_S
RollingRRS(t)   = mean(RRS(t-i) for i in 0..L-1)   -- anti-one-candle-spike

`window` (L) is the price-change lookback (5 bars for D1, 12 for M5) and is
independent of the ATR series' own smoothing period (n=14 D1 / n=50 H1) --
callers pass in an already-computed ATR series (see indicators/atr.py).
"""
import pandas as pd


def price_change(close: pd.Series, window: int) -> pd.Series:
    return close - close.shift(window)


def power_index(benchmark_close: pd.Series, benchmark_atr: pd.Series, window: int) -> pd.Series:
    return price_change(benchmark_close, window) / benchmark_atr


def expected_price_change(power_idx: pd.Series, stock_atr: pd.Series) -> pd.Series:
    return power_idx * stock_atr


def rrs(
    stock_close: pd.Series,
    stock_atr: pd.Series,
    benchmark_close: pd.Series,
    benchmark_atr: pd.Series,
    window: int,
) -> pd.Series:
    """Per-bar RRS (not the rolling/smoothed version -- see rolling_rrs)."""
    stock_pc = price_change(stock_close, window)
    pi = power_index(benchmark_close, benchmark_atr, window)
    epc = expected_price_change(pi, stock_atr)
    return (stock_pc - epc) / stock_atr


def rolling_rrs(rrs_series: pd.Series, window: int) -> pd.Series:
    """Mean of per-bar RRS over `window` bars -- the tradable/gating signal
    (penalizes one-candle spikes, rewards consistent accumulation)."""
    return rrs_series.rolling(window).mean()
