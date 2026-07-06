"""Hermetic page tests: streamlit AppTest + monkeypatched rs_spy.ui.data."""
from datetime import date

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


def test_compare_page_disambiguates_duplicate_labels(monkeypatch):
    rid_a = "11111111-1111-1111-1111-111111111111"
    rid_b = "22222222-2222-2222-2222-222222222222"
    runs = pd.DataFrame([
        {"run_id": rid_a, "label": "baseline", "status": "succeeded",
         "created_at": pd.Timestamp("2026-07-05 10:00"),
         "finished_at": pd.Timestamp("2026-07-05 10:20"),
         "n_trades": 13, "profit_factor": 3.71, "total_pnl": 753.0},
        {"run_id": rid_b, "label": "baseline", "status": "succeeded",
         "created_at": pd.Timestamp("2026-07-05 11:00"),
         "finished_at": pd.Timestamp("2026-07-05 11:20"),
         "n_trades": 7, "profit_factor": 1.9, "total_pnl": 120.0},
    ])
    details = {
        rid_a: dict(_detail_fixture(), run_id=rid_a,
                    metrics={"n_trades": 13, "total_pnl": 753.0}),
        rid_b: dict(_detail_fixture(), run_id=rid_b,
                    metrics={"n_trades": 7, "total_pnl": 120.0}),
    }
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=200: runs)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: details[rid])
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: None)
    at = AppTest.from_function(_run_compare_page)
    at.run()
    options = at.multiselect(key="compare_runs").options
    assert options == ["baseline (11111111)", "baseline (22222222)"]
    at.multiselect(key="compare_runs").set_value(list(options))
    at.run()
    assert not at.exception
    table = at.dataframe[0].value
    assert table.shape[1] == 2   # both runs present as separate columns


def test_compare_page_skips_zero_start_equity(monkeypatch):
    runs = _runs_fixture()
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=200: runs)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: _detail_fixture())
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: pd.Series(
        [0.0, 220.0], index=pd.date_range("2026-07-01", periods=2, tz="UTC")))
    at = AppTest.from_function(_run_compare_page)
    at.run()
    at.multiselect(key="compare_runs").set_value(["baseline"])
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 1   # metrics table renders; zero-start curve skipped
    # no chart rendered: rebasing a zero-start curve would produce inf/nan
    assert len(at.get("vega_lite_chart")) == 0


def _run_scan_page():
    from rs_spy.ui.pages import scan_page
    scan_page()


def test_scan_page_renders_history_funnel_and_snapshot(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "scan_dates", lambda conn: [date(2026, 7, 2)])
    monkeypatch.setattr(data, "passing_history", lambda conn: pd.DataFrame(
        {"scan_date": [date(2026, 7, 2)], "n_passed": [1450]}))
    monkeypatch.setattr(data, "scan_funnel", lambda conn, d: {
        "assets": 14021, "passed": 1450, "fail_listing": 7030})
    monkeypatch.setattr(data, "universe_snapshot", lambda conn, d: pd.DataFrame(
        {"symbol": ["AAPL", "PENNY"], "passed": [True, False],
         "first_fail": [None, "price"]}))
    monkeypatch.setattr(data, "onboarded_df", lambda conn: pd.DataFrame(
        {"symbol": ["HOOD"], "insufficient_history": [False]}))
    at = AppTest.from_function(_run_scan_page)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 2       # assets + passed cards
    assert len(at.dataframe) >= 2    # snapshot browser + onboarded table


def test_scan_page_with_no_scans_yet(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "scan_dates", lambda conn: [])
    at = AppTest.from_function(_run_scan_page)
    at.run()
    assert not at.exception
    assert at.info                    # friendly empty state, no crash


def _run_campaigns_page():
    from rs_spy.ui.pages import campaigns_page
    campaigns_page()


def test_campaigns_page_aggregates_complete_campaigns(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "campaign_groups", lambda conn: pd.DataFrame(
        [{"tag": "jul05", "variant": "baseline", "n_cohorts": 4,
          "statuses": ["succeeded"]}]))
    import rs_spy.ui.pages as pages_mod
    monkeypatch.setattr(pages_mod, "aggregate_campaign", lambda conn, tag, variant: {
        "n_runs": 4,
        "trades": pd.DataFrame({"symbol": ["AAPL"], "pnl": [10.0]}),
        "equity": pd.Series([400.0, 410.0],
                            index=pd.date_range("2026-07-01", periods=2, tz="UTC")),
        "metrics": {"n_trades": 40, "profit_factor": 2.5},
    })
    at = AppTest.from_function(_run_campaigns_page)
    at.run()
    at.selectbox(key="campaign_pick").set_value("jul05 / baseline")
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 2   # groups table + pooled metrics table


def test_campaigns_page_handles_slash_in_tag(monkeypatch):
    # parse_campaign_label's tag group is unrestricted (.+), so a tag may itself
    # contain "/" — the pick round-trip must not crash or mis-split.
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "campaign_groups", lambda conn: pd.DataFrame(
        [{"tag": "q1/26", "variant": "baseline", "n_cohorts": 2,
          "statuses": ["succeeded"]}]))
    import rs_spy.ui.pages as pages_mod
    seen = []
    monkeypatch.setattr(pages_mod, "aggregate_campaign", lambda conn, tag, variant: (
        seen.append((tag, variant)) or {
            "n_runs": 2,
            "trades": pd.DataFrame({"symbol": ["AAPL"], "pnl": [10.0]}),
            "equity": None,
            "metrics": {"n_trades": 20, "profit_factor": 2.0},
        }))
    at = AppTest.from_function(_run_campaigns_page)
    at.run()
    at.selectbox(key="campaign_pick").set_value("q1/26 / baseline")
    at.run()
    assert not at.exception
    assert seen == [("q1/26", "baseline")]
    assert len(at.dataframe) >= 2   # groups table + pooled metrics table
