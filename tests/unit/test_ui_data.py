"""Pure ui.data helpers (no Postgres, no streamlit widgets exercised)."""
import pandas as pd
import pytest

pytest.importorskip("streamlit")  # rs_spy.ui.data imports streamlit (cache_resource)
from rs_spy.ui.data import parse_campaign_label, _headline_row  # noqa: E402


def test_parse_campaign_label_handles_tags_with_dashes():
    assert parse_campaign_label("m10-jul-05-baseline-c2") == ("jul-05", "baseline", 2)
    assert parse_campaign_label("m10-x-w12-c10") == ("x", "w12", 10)


def test_parse_campaign_label_rejects_non_campaign_labels():
    assert parse_campaign_label(None) is None
    assert parse_campaign_label("onboarding-2026-07-06") is None
    assert parse_campaign_label("m10-missing-cohort") is None


def test_headline_row_is_none_safe_for_queued_runs():
    run = {"run_id": "x", "label": "L", "status": "queued",
           "created_at": pd.Timestamp("2026-07-05"), "finished_at": None,
           "metrics": None}
    row = _headline_row(run)
    assert row["n_trades"] is None and row["profit_factor"] is None
    assert row["status"] == "queued"


def test_headline_row_extracts_metrics_when_present():
    run = {"run_id": "x", "label": "L", "status": "succeeded",
           "created_at": pd.Timestamp("2026-07-05"), "finished_at": pd.Timestamp("2026-07-05"),
           "metrics": {"n_trades": 13, "profit_factor": 3.71, "total_pnl": 753.0}}
    row = _headline_row(run)
    assert row["n_trades"] == 13 and row["profit_factor"] == 3.71 and row["total_pnl"] == 753.0


def test_campaign_groups_rolls_up_by_tag_and_variant(monkeypatch):
    import rs_spy.ui.data as data_mod
    rows = [
        {"label": "m10-jul05-baseline-c1", "status": "succeeded"},
        {"label": "m10-jul05-baseline-c2", "status": "running"},
        {"label": "m10-jul05-w12-c1", "status": "succeeded"},
        {"label": "onboarding-2026-07-06", "status": "succeeded"},  # not a campaign
        {"label": None, "status": "failed"},
    ]
    monkeypatch.setattr(data_mod.repo, "list_runs", lambda conn, limit=500: rows)
    df = data_mod.campaign_groups(None)
    assert len(df) == 2
    base = df[(df["tag"] == "jul05") & (df["variant"] == "baseline")].iloc[0]
    assert base["n_cohorts"] == 2
    assert base["statuses"] == ["running", "succeeded"]
