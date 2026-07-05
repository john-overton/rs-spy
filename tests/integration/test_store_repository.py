"""Repository round-trips against a real Postgres. Auto-skips without Docker."""
import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, BacktestResultM5, TradeM5
from rs_spy.store import repository as repo

pytestmark = pytest.mark.integration


def _trade(symbol="AAPL", direction="LONG", pnl=100.0, r=1.5):
    ts = pd.Timestamp("2026-02-02 15:00", tz="UTC")
    return TradeM5(
        symbol=symbol, direction=direction, entry_time=ts, entry_price=100.0,
        exit_time=ts + pd.Timedelta(hours=1), exit_price=101.0, shares=10.0,
        exit_reason="profit_take", pnl=pnl, r_multiple=r,
    )


def _result(trades, equity=None):
    return BacktestResultM5(trades=trades, equity_curve=equity, funnel={"long_orders_filled": len(trades)})


def test_create_run_and_get_run_round_trip(pg_conn):
    config = BacktestConfigM5(shorts_enabled=True, disabled_gates=frozenset({"bias"}))
    run_id = repo.create_run(pg_conn, config, label="my-run", git_sha="abc123")

    row = repo.get_run(pg_conn, run_id)
    assert row["run_id"] == run_id
    assert row["status"] == "queued"
    assert row["label"] == "my-run"
    assert row["git_sha"] == "abc123"
    assert row["config"]["shorts_enabled"] is True
    assert row["config"]["disabled_gates"] == ["bias"]

    # config reconstructs to the exact dataclass
    assert repo.get_config(pg_conn, run_id) == config


def test_status_transitions_queued_running_succeeded(pg_conn):
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    assert repo.get_run(pg_conn, run_id)["status"] == "queued"

    repo.mark_running(pg_conn, run_id, pid=4242, host="testhost")
    row = repo.get_run(pg_conn, run_id)
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["pid"] == 4242 and row["host"] == "testhost"

    idx = pd.date_range("2026-02-02 09:30", periods=5, freq="5min", tz="UTC")
    equity = pd.Series(np.linspace(100_000, 100_500, 5), index=idx, name="equity")
    metrics = {"n_trades": 2, "profit_factor": None, "total_pnl": 200.0}
    repo.save_result(pg_conn, run_id, _result([_trade(), _trade(pnl=100.0)], equity),
                     metrics, same_bar_stop_rate=0.0)

    row = repo.get_run(pg_conn, run_id)
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None
    assert row["metrics"]["total_pnl"] == 200.0
    assert row["funnel"]["long_orders_filled"] == 2
    assert row["funnel"]["same_bar_stop_rate"] == 0.0


def test_save_result_persists_trades_in_order(pg_conn):
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    trades = [_trade(symbol="AAA"), _trade(symbol="BBB"), _trade(symbol="CCC")]
    repo.save_result(pg_conn, run_id, _result(trades), {"n_trades": 3})

    df = repo.get_trades(pg_conn, run_id)
    assert list(df["symbol"]) == ["AAA", "BBB", "CCC"]  # seq order preserved
    assert set(df.columns) == {
        "symbol", "direction", "entry_time", "entry_price", "exit_time",
        "exit_price", "shares", "exit_reason", "pnl", "r_multiple",
    }


def test_save_result_equity_round_trips(pg_conn):
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    idx = pd.date_range("2026-02-02 09:30", periods=50, freq="5min", tz="UTC")
    equity = pd.Series(np.linspace(100_000, 99_000, 50), index=idx, name="equity")
    repo.save_result(pg_conn, run_id, _result([_trade()], equity), {"n_trades": 1})

    restored = repo.get_equity(pg_conn, run_id)
    pd.testing.assert_series_equal(restored, equity, check_freq=False)


def test_save_result_no_trades_no_equity(pg_conn):
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    repo.save_result(pg_conn, run_id, _result([], None), {"n_trades": 0})
    assert repo.get_run(pg_conn, run_id)["status"] == "succeeded"
    assert repo.get_trades(pg_conn, run_id).empty
    assert repo.get_equity(pg_conn, run_id) is None


def test_mark_failed_sets_status_and_error(pg_conn):
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    repo.mark_running(pg_conn, run_id)
    repo.mark_failed(pg_conn, run_id, "ValueError: boom")
    row = repo.get_run(pg_conn, run_id)
    assert row["status"] == "failed"
    assert "boom" in row["error"]
    assert row["finished_at"] is not None


def test_list_runs_filters_and_paginates(pg_conn):
    ids = [repo.create_run(pg_conn, BacktestConfigM5(), label=f"r{i}") for i in range(3)]
    repo.mark_running(pg_conn, ids[0])

    all_runs = repo.list_runs(pg_conn)
    assert len(all_runs) == 3
    # most-recent first
    assert all_runs[0]["label"] == "r2"

    running = repo.list_runs(pg_conn, status="running")
    assert len(running) == 1 and running[0]["run_id"] == ids[0]

    page = repo.list_runs(pg_conn, limit=1, offset=1)
    assert len(page) == 1 and page[0]["label"] == "r1"


def test_save_result_sanitized_inf_would_be_none(pg_conn):
    """JSONB rejects Infinity -- the runner sanitizes first; here we confirm the
    sanitized None persists cleanly."""
    run_id = repo.create_run(pg_conn, BacktestConfigM5())
    repo.save_result(pg_conn, run_id, _result([_trade()]),
                     {"profit_factor": None, "n_trades": 1})
    assert repo.get_run(pg_conn, run_id)["metrics"]["profit_factor"] is None
