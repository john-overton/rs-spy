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
