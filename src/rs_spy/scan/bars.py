"""Broad-scan daily-bar storage + refresh.

A separate DuckDB file (Settings.resolved_scan_warehouse_path, default
data/scan.duckdb) with the exact same bars/fetch_manifest schema as the main
warehouse -- warehouse.connect() is reused as-is.

Refresh strategy: the manifest-driven backfill covers history idempotently,
but a calendar-year manifest unit is marked done at first fetch and goes
stale as the current year grows. refresh_daily_bars therefore always
re-fetches a recent tail unconditionally and upserts it (bars upserts are
idempotent). The tail start self-heals to the newest stored bar, so a run
after any outage catches up in one pass.
"""
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from rs_spy.data.ingest import _batches, _write_bars, backfill
from rs_spy.data.warehouse import connect


def connect_scan(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the scan warehouse (same schema/DDL as the main warehouse)."""
    return connect(path, read_only=read_only)


def refresh_daily_bars(
    con: duckdb.DuckDBPyConnection,
    client,
    symbols: list[str],
    end: datetime,
    *,
    years: int = 5,
    tail_days: int = 7,
    symbol_batch_size: int = 200,
) -> None:
    """Bring the scan warehouse's daily bars up to date through `end`.

    1. Manifest-driven historical backfill over [end - years, end) -- cheap
       no-op for every already-done (symbol, year) unit.
    2. Unconditional tail re-fetch from min(end - tail_days, newest stored
       bar) -- picks up days the current-year manifest unit can't see. The
       "newest stored bar" is scoped to `symbols` (the symbols passed to this
       call), not the whole table: other, fresher symbols in the same
       warehouse must not mask genuine staleness in the requested subset.
    """
    start = end - timedelta(days=365 * years + 5)
    backfill(
        con, client, symbols, "day", start, end,
        chunk_freq="year", symbol_batch_size=symbol_batch_size,
    )

    tail_start = end - timedelta(days=tail_days)
    latest = con.execute(
        "SELECT max(ts) FROM bars WHERE timespan = 'day' "
        "AND symbol IN (SELECT unnest(?::VARCHAR[]))",
        [symbols],
    ).fetchone()[0]
    if latest is not None:
        latest_dt = pd.Timestamp(latest).tz_localize("UTC").to_pydatetime()
        tail_start = min(tail_start, latest_dt)
    for batch in _batches(symbols, symbol_batch_size):
        df = client.fetch_bars(batch, "day", tail_start, end)
        _write_bars(con, df)
