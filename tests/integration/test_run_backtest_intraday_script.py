"""Confirms the script's helper wiring works end-to-end against a small
synthetic universe -- this is NOT a network test (no Alpaca calls); it builds an
in-memory DuckDB warehouse directly, same pattern as
tests/integration/test_cache_resume.py."""
import numpy as np
import pandas as pd
import pytest


def _write_minute_bars(con, symbol: str, dates: list[str], seed: int) -> None:
    rng = np.random.default_rng(seed)
    rows = []
    for d in dates:
        idx = pd.date_range(f"{d} 09:30", periods=390, freq="1min", tz="America/New_York").tz_convert("UTC")
        close = 100.0 + np.cumsum(rng.normal(0, 0.05, 390))
        for ts, c in zip(idx, close):
            rows.append((symbol, "minute", ts.to_pydatetime(), c - 0.02, c + 0.05, c - 0.05, c, 1000.0))
    con.executemany(
        "INSERT INTO bars (symbol, timespan, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


@pytest.fixture
def warehouse(tmp_path):
    from rs_spy.data.warehouse import connect

    con = connect(tmp_path / "test.duckdb")
    dates = [f"2026-02-{2 + i:02d}" for i in range(10)]
    _write_minute_bars(con, "SPY", dates, seed=1)
    _write_minute_bars(con, "QQQ", dates, seed=2)
    _write_minute_bars(con, "AAPL", dates, seed=3)
    yield con
    con.close()


def test_load_universe_m1_bars_exists_and_returns_a_dict_of_dataframes(warehouse):
    from rs_spy.data.loader import load_universe_m1_bars

    result = load_universe_m1_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    assert set(result.keys()) == {"SPY", "QQQ", "AAPL"}
    assert not result["SPY"].empty


def test_run_backtest_intraday_script_main_runs_end_to_end(warehouse):
    from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
    from rs_spy.data.loader import (
        load_universe_m1_bars,
        load_universe_m5_bars,
    )

    m1 = load_universe_m1_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    m5 = load_universe_m5_bars(warehouse, ["SPY", "QQQ", "AAPL"])
    daily = {
        sym: df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        for sym, df in m1.items()
    }
    for sym in daily:
        daily[sym].index = daily[sym].index.tz_localize(None)

    result = run_m5_backtest(
        universe_m1={"AAPL": m1["AAPL"]},
        universe_m5={"AAPL": m5["AAPL"]},
        universe_d1={"AAPL": daily["AAPL"]},
        spy_m1=m1["SPY"], spy_m5=m5["SPY"], spy_d1=daily["SPY"],
        qqq_m1=m1["QQQ"], qqq_m5=m5["QQQ"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert result.equity_curve is not None
