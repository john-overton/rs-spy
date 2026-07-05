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
