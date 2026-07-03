"""Causality tests: for every indicator, truncating history to "as of bar i"
and recomputing must reproduce exactly what the full-history computation
says bar i's value was. Catches accidental centered windows, `.shift(-n)`,
or any use of bars the algorithm wouldn't have had live.

pivot_highs/pivot_lows are deliberately excluded: they use centered windows
to *detect* a historical pivot and are NOT causal in isolation by design
(see indicators/headroom.py docstring). What matters is that indicators built
on top of them (headroom_long/short, down/up_trendline) only ever *use* a
pivot once "confirmed" -- this test proves that confirmation-lag design
actually prevents leakage in the composed indicators, which is the real risk.

follow_through() is also excluded: it's a retrospective audit of a past
breakout that deliberately inspects future bars, called after the fact, not
a live per-bar signal.
"""
import numpy as np
import pandas as pd

from rs_spy.indicators.atr import atr
from rs_spy.indicators.candle_structure import overlap_ratio, stacked_count
from rs_spy.indicators.headroom import headroom_long, headroom_short
from rs_spy.indicators.heikin_ashi import ha_continuation
from rs_spy.indicators.laguerre_rsi import laguerre_rsi
from rs_spy.indicators.rrs import rolling_rrs, rrs
from rs_spy.indicators.rvol import rvol
from rs_spy.indicators.sma_stack import sma_stack
from rs_spy.indicators.trendlines import down_trendline, up_trendline
from rs_spy.indicators.vwap import vwap

_N = 250
_CHECK_INDICES = [30, 60, 90, 120, 150, 180, 210, 240, 249]


def _synthetic_intraday(n_sessions=25, bars_per_session=15, seed=13):
    rng = np.random.default_rng(seed)
    n = n_sessions * bars_per_session
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = close - rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.5, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.5, n))
    volume = rng.integers(1000, 5000, n).astype(float)
    index = []
    for s in range(n_sessions):
        day = pd.Timestamp("2024-01-02", tz="UTC") + pd.Timedelta(days=s)
        index.extend(day + pd.Timedelta(minutes=m) for m in range(bars_per_session))
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.DatetimeIndex(index),
    )
    return df, bars_per_session


def _synthetic_ohlcv(n=_N, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = close - rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.5, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.5, n))
    volume = rng.integers(1000, 5000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def _assert_matches(full_val, truncated_val):
    if full_val is None or truncated_val is None:
        assert full_val == truncated_val
        return
    if isinstance(full_val, str):
        assert full_val == truncated_val
        return
    if pd.isna(full_val) and pd.isna(truncated_val):
        return
    np.testing.assert_allclose([truncated_val], [full_val], rtol=1e-9)


def _assert_causal(df: pd.DataFrame, fn) -> None:
    full = fn(df)
    for i in _CHECK_INDICES:
        truncated = fn(df.iloc[: i + 1])
        _assert_matches(full.iloc[i], truncated.iloc[-1])


def test_atr_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: atr(d, n=14))


def test_sma_stack_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: sma_stack(d))


def test_ha_continuation_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: ha_continuation(d, atr(d, n=14)))


def test_stacked_count_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: stacked_count(d, volume_lookback=20))


def test_overlap_ratio_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, overlap_ratio)


def test_headroom_long_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: headroom_long(d, atr(d, n=14), strength=5, lookback=60))


def test_headroom_short_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: headroom_short(d, atr(d, n=14), strength=5, lookback=60))


def test_down_trendline_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: down_trendline(d, strength=3, min_gap=6))


def test_up_trendline_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: up_trendline(d, strength=3, min_gap=6))


def test_rolling_rrs_is_causal():
    df = _synthetic_ohlcv()
    base = pd.Series(df["close"] - df["close"].shift(1)).fillna(0)
    _assert_causal(df.assign(_rrs=base), lambda d: rolling_rrs(d["_rrs"], window=12))


def test_laguerre_rsi_is_causal():
    df = _synthetic_ohlcv()
    _assert_causal(df, lambda d: laguerre_rsi(d["close"]))


def test_vwap_is_causal():
    df, _ = _synthetic_intraday()
    _assert_causal(df, vwap)


def test_rvol_is_causal():
    df, bars_per_session = _synthetic_intraday(n_sessions=25, bars_per_session=15)
    # session 21 (0-indexed 20) is the first with a full 20-session lookback.
    check_positions = [20 * bars_per_session + 5, 24 * bars_per_session + 10]
    full = rvol(df)
    for i in check_positions:
        truncated = rvol(df.iloc[: i + 1])
        _assert_matches(full.iloc[i], truncated.iloc[-1])


def test_rrs_is_causal_for_stock_and_benchmark():
    stock = _synthetic_ohlcv(seed=7)
    bench = _synthetic_ohlcv(seed=11)
    stock_atr = atr(stock, n=14)
    bench_atr = atr(bench, n=14)

    def compute(n_bars: int) -> pd.Series:
        return rrs(
            stock["close"].iloc[:n_bars],
            stock_atr.iloc[:n_bars],
            bench["close"].iloc[:n_bars],
            bench_atr.iloc[:n_bars],
            window=5,
        )

    full = compute(_N)
    for i in _CHECK_INDICES:
        truncated = compute(i + 1)
        _assert_matches(full.iloc[i], truncated.iloc[-1])
