"""Campaign aggregation: pooling, equity combination, refusal on partial campaigns."""
import uuid
from unittest.mock import patch

import pandas as pd
import pytest

from rs_spy.backtest.aggregate import (
    CampaignIncompleteError,
    aggregate_campaign,
    find_campaign_runs,
)

DAYS = pd.date_range("2026-01-05", periods=3, freq="B", tz="UTC")


def _trades(symbol, pnl):
    return pd.DataFrame({
        "symbol": [symbol], "direction": ["long"],
        "entry_time": [DAYS[0]], "entry_price": [10.0],
        "exit_time": [DAYS[1]], "exit_price": [11.0],
        "shares": [10], "exit_reason": ["profit_target"],
        "pnl": [pnl], "r_multiple": [1.0],
    })


def _runs(statuses):
    return [
        {"run_id": uuid.uuid4(), "label": f"m10-t-baseline-c{i+1}", "status": s}
        for i, s in enumerate(statuses)
    ]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params):
        assert "label LIKE" in sql and params == ("m10-t-baseline-c%",)
    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows
    def cursor(self):
        return _Cursor(self._rows)


def test_find_campaign_runs_queries_by_label_pattern():
    rows = _runs(["succeeded"])
    assert find_campaign_runs(_Conn(rows), "t", "baseline") == rows


def test_find_campaign_runs_post_filters_spurious_like_matches():
    # A real `LIKE 'm10-t-baseline-c%'` over-matches: the trailing c% wildcard
    # also swallows a *different* variant's label, e.g. "baseline-cool-w12".
    # find_campaign_runs must post-filter these out with the exact regex, or
    # a differently-varianted cohort would be silently pooled in.
    good = _runs(["succeeded"])[0]
    spurious = {
        "run_id": uuid.uuid4(), "label": "m10-t-baseline-cool-w12-c1", "status": "succeeded",
    }
    assert find_campaign_runs(_Conn([good, spurious]), "t", "baseline") == [good]


def test_aggregate_refuses_missing_and_unfinished_campaigns():
    with pytest.raises(CampaignIncompleteError):
        aggregate_campaign(_Conn([]), "t", "baseline")
    with pytest.raises(CampaignIncompleteError, match="running"):
        aggregate_campaign(_Conn(_runs(["succeeded", "running"])), "t", "baseline")
    with pytest.raises(CampaignIncompleteError, match="failed"):
        aggregate_campaign(_Conn(_runs(["succeeded", "failed"])), "t", "baseline")


def test_aggregate_pools_trades_and_sums_equity():
    runs = _runs(["succeeded", "succeeded"])
    conn = _Conn(runs)
    eq1 = pd.Series([100.0, 101.0], index=DAYS[:2])
    eq2 = pd.Series([100.0, 99.0, 103.0], index=DAYS)  # longer index

    def fake_trades(c, rid):
        return _trades("AAA", 10.0) if rid == runs[0]["run_id"] else _trades("BBB", -5.0)

    def fake_equity(c, rid):
        return eq1 if rid == runs[0]["run_id"] else eq2

    with patch("rs_spy.backtest.aggregate.get_trades", side_effect=fake_trades), \
         patch("rs_spy.backtest.aggregate.get_equity", side_effect=fake_equity):
        out = aggregate_campaign(conn, "t", "baseline")

    assert out["n_runs"] == 2
    assert sorted(out["trades"]["symbol"]) == ["AAA", "BBB"]
    # union index, shorter curve ffilled: day3 = 101 (ffill) + 103 = 204
    assert out["equity"].loc[DAYS[2]] == pytest.approx(204.0)
    assert out["metrics"]["n_trades"] == 2
    assert out["metrics"]["total_pnl"] == pytest.approx(5.0)
