import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5


def _m1_session(date: str, n_minutes: int, start_price: float, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{date} 09:30", periods=n_minutes, freq="1min", tz="America/New_York").tz_convert("UTC")
    noise = rng.normal(0, 0.05, n_minutes)
    close = start_price + np.cumsum(np.full(n_minutes, drift) + noise)
    high = close + np.abs(rng.normal(0.05, 0.02, n_minutes))
    low = close - np.abs(rng.normal(0.05, 0.02, n_minutes))
    open_ = close - drift - noise
    volume = rng.integers(500, 1500, n_minutes).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _build_m1(dates, n_minutes=390, start_price=100.0, drift=0.0, seed=1):
    frames = [_m1_session(d, n_minutes, start_price + i * drift * n_minutes, drift, seed + i) for i, d in enumerate(dates)]
    return pd.concat(frames)


def _build_d1(m1: pd.DataFrame) -> pd.DataFrame:
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_localize(None)
    return daily


# 30 trading-day-like sessions. Built via bdate_range (not a literal
# "2026-02-{02+i}" format string) because that naive formatting overflows
# February's 28 days partway through a 30-session run and produces invalid
# calendar dates (e.g. 2026-02-29..31).
DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=30)]


@pytest.fixture
def universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    qqq_m1 = _build_m1(DATES, drift=0.0006, seed=2)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)

    spy_m5 = resample_ohlcv(spy_m1, "5min")
    qqq_m5 = resample_ohlcv(qqq_m1, "5min")
    aapl_m5 = resample_ohlcv(aapl_m1, "5min")

    spy_d1 = _build_d1(spy_m1)
    qqq_d1 = _build_d1(qqq_m1)
    aapl_d1 = _build_d1(aapl_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "qqq_m1": qqq_m1, "qqq_m5": qqq_m5, "qqq_d1": qqq_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
    }


def test_prepare_m5_returns_calendar_matching_spy_m5_index(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.calendar.equals(universe["spy_m5"].index)
    assert "raw_score" in prepared.bias_df.columns
    assert "regime_d1_m5" not in prepared.bias_df.columns  # lives on PreparedM5 directly


def test_prepare_m5_per_symbol_outputs_are_reindexed_onto_the_master_calendar(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    for attr in ("bars", "features", "ema8", "atr_m5", "adv20_m5", "gate_long", "gate_short",
                 "score_long", "score_short", "rs_failure_long", "rs_failure_short",
                 "vwap_loss_long", "vwap_loss_short", "momentum_stall_long", "momentum_stall_short",
                 "confirm_trigger_long", "confirm_trigger_short", "dip_quality_long",
                 "bounce_quality_short", "squeeze_guard_short"):
        series_or_df = getattr(prepared, attr)["AAPL"]
        assert series_or_df.index.equals(prepared.calendar), f"{attr} not reindexed onto master calendar"


def test_prepare_m5_gates_are_bool_dtype_with_no_nan_after_reindex(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    gl = prepared.gate_long["AAPL"]
    assert gl.dtype == bool
    assert not gl.isna().any()


def test_prepare_m5_regime_d1_m5_is_a_single_market_wide_series(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.regime_d1_m5.index.equals(prepared.calendar)
