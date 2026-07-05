"""Postgres data layer for the UI. Thin wrappers over store/* so pages stay
render-only. Pages must call these as `data.fn(...)` module attributes --
tests monkeypatch this module and never need Postgres."""
import re
import uuid

import pandas as pd
import streamlit as st

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.config import get_settings
from rs_spy.jobs.launch import launch_run
from rs_spy.store import repository as repo
from rs_spy.store import scan_repository as scan_repo
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema

_RUN_COLS = ["run_id", "label", "status", "created_at", "finished_at",
             "n_trades", "profit_factor", "total_pnl"]
_CAMPAIGN_RE = re.compile(r"^m10-(.+)-([A-Za-z0-9_]+)-c(\d+)$")


@st.cache_resource
def get_conn():
    conn = connect_pg(get_settings().database_url)
    init_schema(conn)
    return conn


def _headline_row(run: dict) -> dict:
    m = run.get("metrics") or {}
    return {
        "run_id": run["run_id"], "label": run["label"], "status": run["status"],
        "created_at": run["created_at"], "finished_at": run["finished_at"],
        "n_trades": m.get("n_trades"), "profit_factor": m.get("profit_factor"),
        "total_pnl": m.get("total_pnl"),
    }


def runs_df(conn, limit: int = 50, offset: int = 0) -> pd.DataFrame:
    rows = repo.list_runs(conn, limit=limit, offset=offset)
    return pd.DataFrame([_headline_row(r) for r in rows], columns=_RUN_COLS)


def run_detail(conn, run_id) -> dict | None:
    return repo.get_run(conn, uuid.UUID(str(run_id)))


def trades_df(conn, run_id) -> pd.DataFrame:
    return repo.get_trades(conn, uuid.UUID(str(run_id)))


def equity_series(conn, run_id) -> pd.Series | None:
    return repo.get_equity(conn, uuid.UUID(str(run_id)))


def config_of(conn, run_id) -> BacktestConfigM5:
    return repo.get_config(conn, uuid.UUID(str(run_id)))


def create_and_launch(conn, config: BacktestConfigM5, label: str | None) -> uuid.UUID:
    run_id = repo.create_run(conn, config, label=label or None)
    launch_run(run_id)
    return run_id


def parse_campaign_label(label) -> tuple[str, str, int] | None:
    if not label:
        return None
    m = _CAMPAIGN_RE.match(label)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def scan_dates(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT scan_date FROM scan_runs ORDER BY scan_date DESC")
        return [r["scan_date"] for r in cur.fetchall()]


def passing_history(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scan_date, (funnel->>'passed')::int AS n_passed "
            "FROM scan_runs ORDER BY scan_date"
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["scan_date", "n_passed"])


def scan_funnel(conn, scan_date) -> dict | None:
    return scan_repo.get_scan_funnel(conn, scan_date)


def universe_snapshot(conn, scan_date) -> pd.DataFrame:
    return scan_repo.get_universe_snapshot(conn, scan_date)


def onboarded_df(conn) -> pd.DataFrame:
    return scan_repo.list_onboarded(conn)
