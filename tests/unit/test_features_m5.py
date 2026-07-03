import numpy as np
import pandas as pd
import pytest

from rs_spy.indicators.rvol import rvol as rvol_fn
from rs_spy.indicators.vwap import vwap as vwap_fn
from rs_spy.selection.features_m5 import compute_symbol_features_m5


def _rth_m1(n_sessions, bars_per_session, base, drift_per_bar=0.0, seed=7):
    rng = np.random.default_rng(seed)
    frames = []
    for s in range(n_sessions):
        day = pd.Timestamp("2024-06-03", tz="UTC") + pd.Timedelta(days=s)
        idx = pd.date_range(day.replace(hour=13, minute=30), periods=bars_per_session, freq="1min")
        close = base + np.arange(bars_per_session) * drift_per_bar + rng.normal(0, 0.05, bars_per_session)
        frames.append(
            pd.DataFrame(
                {
                    "open": close - 0.02,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": rng.uniform(500, 1500, bars_per_session),
                },
                index=idx,
            )
        )
        base = close[-1]
    return pd.concat(frames)


def _d1_from_m1(m1: pd.DataFrame) -> pd.DataFrame:
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    daily = daily.dropna(subset=["open"])
    daily.index = daily.index.tz_localize(None)
    return daily


def test_output_has_expected_columns():
    spy_m1 = _rth_m1(n_sessions=60, bars_per_session=390, base=500.0, seed=1)
    stock_m1 = _rth_m1(n_sessions=60, bars_per_session=390, base=100.0, seed=2)
    spy_m5 = spy_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    stock_m5 = stock_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    spy_d1 = _d1_from_m1(spy_m1)
    stock_d1 = _d1_from_m1(stock_m1)

    out = compute_symbol_features_m5(stock_m1, stock_m5, stock_d1, spy_m1, spy_m5, spy_d1)

    for col in ["rrs_m5", "rolling_rrs_m5", "vwap_m5", "rvol_m5", "lrsi_m5", "one_candle_wonder", "gap_pct",
                "ha_cont_d1", "sma_stack", "headroom_long", "headroom_short"]:
        assert col in out.columns, col
    assert out.index.equals(stock_m5.index)


def test_rrs_m5_is_strongly_positive_when_stock_outruns_flat_spy():
    # SPY dead flat, stock steadily grinding up -- stock's RRS vs SPY should
    # be clearly positive once the RRS window has enough history.
    spy_m1 = _rth_m1(n_sessions=15, bars_per_session=390, base=500.0, drift_per_bar=0.0, seed=3)
    stock_m1 = _rth_m1(n_sessions=15, bars_per_session=390, base=100.0, drift_per_bar=0.01, seed=4)
    spy_m5 = spy_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    stock_m5 = stock_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    spy_d1 = _d1_from_m1(spy_m1)
    stock_d1 = _d1_from_m1(stock_m1)

    out = compute_symbol_features_m5(stock_m1, stock_m5, stock_d1, spy_m1, spy_m5, spy_d1)
    tail = out["rolling_rrs_m5"].dropna()
    assert len(tail) > 0
    assert tail.iloc[-1] > 0.5


def test_vwap_and_rvol_m5_do_not_leak_the_next_minute_bar():
    # Regression test for a real off-by-one lookahead bug: raw M1 bars are
    # open-labeled (timestamp = interval start -- data/session.py's RTH mask
    # covers 09:30-15:59 inclusive, so the 09:30 bar covers [09:30, 09:31)),
    # while M5 bars built by data.resample.resample_ohlcv are close-labeled
    # (timestamp = interval end -- an M5 bar labeled 13:35 covers
    # [13:30, 13:35), confirmed by resample_ohlcv's own docstring/tests).
    # Naively align_causal-ing an M1-cadence series straight onto the M5
    # index picks up the M1 bar that STARTS exactly at the M5 bar's own
    # close-label timestamp -- i.e. one minute of data that bar hasn't seen
    # yet -- unless the M1 index is first converted to the same close-label
    # convention.
    stock_m1 = _rth_m1(n_sessions=25, bars_per_session=390, base=100.0, seed=5)
    spy_m1 = _rth_m1(n_sessions=25, bars_per_session=390, base=500.0, seed=6)
    stock_m5 = stock_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    spy_m5 = spy_m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    stock_d1 = _d1_from_m1(stock_m1)
    spy_d1 = _d1_from_m1(spy_m1)

    out = compute_symbol_features_m5(stock_m1, stock_m5, stock_d1, spy_m1, spy_m5, spy_d1)

    vwap_m1_native = vwap_fn(stock_m1)
    rvol_m1_native = rvol_fn(stock_m1)

    bucket_ts = stock_m5.index[len(stock_m5) // 2]
    correct_minute = bucket_ts - pd.Timedelta(minutes=1)  # last minute strictly inside the M5 bucket
    leaked_minute = bucket_ts  # first minute of the NEXT M5 bucket -- must not be visible yet

    assert out.loc[bucket_ts, "vwap_m5"] == pytest.approx(vwap_m1_native.loc[correct_minute])
    assert out.loc[bucket_ts, "vwap_m5"] != pytest.approx(vwap_m1_native.loc[leaked_minute])

    assert out.loc[bucket_ts, "rvol_m5"] == pytest.approx(rvol_m1_native.loc[correct_minute], nan_ok=True)
    assert out.loc[bucket_ts, "rvol_m5"] != pytest.approx(rvol_m1_native.loc[leaked_minute])
