"""Pool a campaign variant's cohort runs into one metrics view (M10).

Refuses partial campaigns: pooling 3 of 4 cohorts would silently understate
sample size and overstate whichever cohorts happened to finish. Equity across
cohorts is combined as a SUM on the union index (each cohort trades its own
capital in its own process); per-cohort curves are ffilled to the union --
a documented approximation, fine for drawdown shape, not a portfolio sim.
"""
import pandas as pd

from rs_spy.backtest.campaign import campaign_label_re
from rs_spy.backtest.metrics import compute_metrics
from rs_spy.store.repository import get_equity, get_trades


class CampaignIncompleteError(RuntimeError):
    """Campaign has missing/unfinished/failed cohort runs -- refuse to pool."""


def find_campaign_runs(conn, tag: str, variant: str) -> list[dict]:
    """Runs for this (tag, variant) campaign. The LIKE clause is a cheap SQL
    pre-filter (prefix-unanchored, `_`/`%`-unescaped); rows are post-filtered
    through campaign_label_re for the real, exact match -- see that
    function's docstring for why (e.g. a "baseline" query must not pool in a
    "baseline-cool-w12" cohort's run)."""
    pattern = campaign_label_re(tag, variant)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM runs WHERE label LIKE %s ORDER BY label",
            (f"m10-{tag}-{variant}-c%",),
        )
        rows = cur.fetchall()
    return [r for r in rows if pattern.fullmatch(r["label"])]


def aggregate_campaign(conn, tag: str, variant: str) -> dict:
    runs = find_campaign_runs(conn, tag, variant)
    if not runs:
        raise CampaignIncompleteError(f"no runs labeled m10-{tag}-{variant}-c*")
    bad = {r["label"]: r["status"] for r in runs if r["status"] != "succeeded"}
    if bad:
        raise CampaignIncompleteError(f"non-succeeded cohort runs: {bad}")

    all_trades = [get_trades(conn, r["run_id"]) for r in runs]
    trades = (
        pd.concat(all_trades, ignore_index=True)
        .sort_values("entry_time")
        .reset_index(drop=True)
    )

    curves = [eq for r in runs if (eq := get_equity(conn, r["run_id"])) is not None]
    equity = None
    if curves:
        union = curves[0].index
        for c in curves[1:]:
            union = union.union(c.index)
        equity = sum(c.reindex(union).ffill().bfill() for c in curves)

    trading_days = len(equity.index.normalize().unique()) if equity is not None else 0
    metrics = compute_metrics(trades, equity, trading_days)
    return {"n_runs": len(runs), "trades": trades, "equity": equity, "metrics": metrics}
