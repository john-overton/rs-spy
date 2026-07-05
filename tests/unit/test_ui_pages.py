"""Hermetic page tests: streamlit AppTest + monkeypatched rs_spy.ui.data."""
import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import rs_spy.ui.data as data  # noqa: E402


EMPTY_RUNS = pd.DataFrame(
    columns=["run_id", "label", "status", "created_at", "finished_at",
             "n_trades", "profit_factor", "total_pnl"]
)


def _stub_common(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=50, offset=0: EMPTY_RUNS)


def test_runs_page_renders_empty_store(monkeypatch):
    _stub_common(monkeypatch)
    at = AppTest.from_function(_run_runs_page)
    at.run()
    assert not at.exception


def _run_runs_page():
    from rs_spy.ui.pages import runs_page
    runs_page()


def _runs_fixture():
    return pd.DataFrame([
        {"run_id": "11111111-1111-1111-1111-111111111111", "label": "baseline",
         "status": "succeeded", "created_at": pd.Timestamp("2026-07-05 10:00"),
         "finished_at": pd.Timestamp("2026-07-05 10:20"),
         "n_trades": 13, "profit_factor": 3.71, "total_pnl": 753.0},
        {"run_id": "22222222-2222-2222-2222-222222222222", "label": "w12",
         "status": "running", "created_at": pd.Timestamp("2026-07-05 11:00"),
         "finished_at": None, "n_trades": None, "profit_factor": None, "total_pnl": None},
    ])


def test_runs_page_renders_table_and_show_more(monkeypatch):
    calls = []

    def fake_runs_df(conn, limit=50, offset=0):
        calls.append(limit)
        return _runs_fixture()

    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", fake_runs_df)
    at = AppTest.from_function(_run_runs_page)
    at.run()
    assert not at.exception
    assert calls and calls[0] == 50          # default page size
    assert len(at.dataframe) >= 1            # the runs table rendered
    at.button(key="show_more").click().run()
    assert calls[-1] == 100                  # limit grew by 50


def _detail_fixture(status="succeeded", error=None):
    return {
        "run_id": "11111111-1111-1111-1111-111111111111", "label": "baseline",
        "status": status, "created_at": pd.Timestamp("2026-07-05 10:00"),
        "finished_at": pd.Timestamp("2026-07-05 10:20"), "error": error,
        "metrics": {"n_trades": 2, "profit_factor": 2.0, "total_pnl": 10.0},
        "funnel": {"eval_long": 100, "filled": 2},
        "config": {"rrs_m5_window": 18},
    }


def _run_detail_page():
    import streamlit as st
    from rs_spy.ui.pages import render_run_detail
    st.session_state.setdefault("noop", True)
    render_run_detail("11111111-1111-1111-1111-111111111111")


def test_run_detail_renders_metrics_trades_equity_and_funnel(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: _detail_fixture())
    monkeypatch.setattr(data, "trades_df", lambda conn, rid: pd.DataFrame(
        {"symbol": ["AAPL"], "pnl": [10.0]}))
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: pd.Series(
        [100.0, 110.0], index=pd.date_range("2026-07-01", periods=2, tz="UTC")))
    at = AppTest.from_function(_run_detail_page)
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 3   # metrics + trades + funnel tables


def test_run_detail_shows_error_for_failed_runs(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "run_detail",
                        lambda conn, rid: _detail_fixture("failed", error="boom"))
    monkeypatch.setattr(data, "trades_df", lambda conn, rid: pd.DataFrame())
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: None)
    at = AppTest.from_function(_run_detail_page)
    at.run()
    assert not at.exception
    assert any("boom" in e.value for e in at.error)


def _run_configure_page():
    from rs_spy.ui.pages import configure_page
    configure_page()


def test_configure_page_submits_defaults_and_launches(monkeypatch):
    launched = []
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(
        data, "create_and_launch",
        lambda conn, config, label: launched.append((config, label)) or
        "33333333-3333-3333-3333-333333333333",
    )
    at = AppTest.from_function(_run_configure_page)
    at.run()
    assert not at.exception
    at.text_input(key="run_label").set_value("my-run")
    at.button(key="FormSubmitter:config_form-Run").click().run()
    assert not at.exception
    from rs_spy.backtest.engine_m5 import BacktestConfigM5
    config, label = launched[0]
    assert config == BacktestConfigM5()   # untouched form launches pure defaults
    assert label == "my-run"


def _run_compare_page():
    from rs_spy.ui.pages import compare_page
    compare_page()


def test_compare_page_renders_side_by_side_metrics(monkeypatch):
    runs = _runs_fixture()
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=200: runs)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: _detail_fixture())
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: pd.Series(
        [200.0, 220.0], index=pd.date_range("2026-07-01", periods=2, tz="UTC")))
    at = AppTest.from_function(_run_compare_page)
    at.run()
    at.multiselect(key="compare_runs").set_value(["baseline"])
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 1   # the metrics comparison table
