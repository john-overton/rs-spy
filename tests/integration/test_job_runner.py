"""Job-runner orchestration against real Postgres. Auto-skips without Docker.

Monkeypatches the actual backtest (_execute_backtest) so these tests exercise
run_job's status lifecycle + crash-safety without needing a 3.4GB warehouse.
The real backtest wiring is covered by test_run_backtest_intraday_script.py.
"""
import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, BacktestResultM5, TradeM5
from rs_spy.jobs import runner
from rs_spy.store import repository as repo

pytestmark = pytest.mark.integration


def _fake_execution():
    ts = pd.Timestamp("2026-02-02 15:00", tz="UTC")
    trade = TradeM5("AAPL", "LONG", ts, 100.0, ts + pd.Timedelta(hours=1), 101.0,
                    10.0, "profit_take", 100.0, 1.5)
    idx = pd.date_range("2026-02-02 09:30", periods=10, freq="5min", tz="UTC")
    equity = pd.Series(np.linspace(100_000, 100_100, 10), index=idx, name="equity")
    result = BacktestResultM5(trades=[trade], equity_curve=equity, funnel={"long_orders_filled": 1})
    metrics = {"n_trades": 1, "profit_factor": None, "total_pnl": 100.0}
    return result, metrics, 0.0


def test_run_job_creates_and_completes_a_run(pg_conn, monkeypatch, pg_url):
    monkeypatch.setattr(runner, "_execute_backtest", lambda config: _fake_execution())

    run_id = runner.run_job(config=BacktestConfigM5(shorts_enabled=True),
                            label="job-test", database_url=pg_url)

    row = repo.get_run(pg_conn, run_id)
    assert row["status"] == "succeeded"
    assert row["label"] == "job-test"
    assert row["started_at"] is not None and row["finished_at"] is not None
    assert row["pid"] is not None and row["host"] is not None
    assert row["metrics"]["total_pnl"] == 100.0
    assert len(repo.get_trades(pg_conn, run_id)) == 1
    assert repo.get_equity(pg_conn, run_id) is not None


def test_run_job_executes_an_existing_queued_run(pg_conn, monkeypatch, pg_url):
    monkeypatch.setattr(runner, "_execute_backtest", lambda config: _fake_execution())
    # UI-style: create the queued row first, then the job runs it by id.
    run_id = repo.create_run(pg_conn, BacktestConfigM5(), label="pre-created")
    assert repo.get_run(pg_conn, run_id)["status"] == "queued"

    returned = runner.run_job(run_id=run_id, database_url=pg_url)
    assert returned == run_id
    assert repo.get_run(pg_conn, run_id)["status"] == "succeeded"


def test_run_job_marks_failed_and_reraises_on_error(pg_conn, monkeypatch, pg_url):
    def boom(config):
        raise ValueError("kaboom")

    monkeypatch.setattr(runner, "_execute_backtest", boom)

    with pytest.raises(ValueError, match="kaboom"):
        runner.run_job(config=BacktestConfigM5(), database_url=pg_url)

    # the failed run was recorded with its error before the raise propagated
    runs = repo.list_runs(pg_conn, status="failed")
    assert len(runs) == 1
    assert "kaboom" in runs[0]["error"]
    assert runs[0]["finished_at"] is not None


def test_run_job_requires_run_id_or_config(pg_conn, pg_url):
    with pytest.raises(ValueError, match="run_id or config"):
        runner.run_job(database_url=pg_url)
