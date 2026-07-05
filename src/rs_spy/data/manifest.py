"""Fetch-manifest bookkeeping: "have we already fetched this (symbol, timespan,
year) unit of work?" -- the resumability mechanism described in the plan.

A unit is only 'ok' or 'empty' once its bar rows (if any) have been durably
written. 'error' units are retried on the next invocation; 'ok'/'empty' are
never re-fetched.
"""
from datetime import datetime, timezone

import duckdb

_DONE_STATUSES = {"ok", "empty"}


def record(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    timespan: str,
    unit_key: str,
    status: str,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO fetch_manifest (symbol, timespan, unit_key, status, row_count, error, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, timespan, unit_key)
        DO UPDATE SET status = excluded.status,
                       row_count = excluded.row_count,
                       error = excluded.error,
                       fetched_at = excluded.fetched_at
        """,
        [symbol, timespan, unit_key, status, row_count, error, datetime.now(timezone.utc)],
    )


def pending_symbols(
    con: duckdb.DuckDBPyConnection,
    symbols: list[str],
    timespan: str,
    unit_key: str,
) -> list[str]:
    """Symbols in `symbols` that do not yet have a done (ok/empty) record
    for this (timespan, unit_key). Order-preserving."""
    if not symbols:
        return []
    placeholders = ",".join(["?"] * len(symbols))
    rows = con.execute(
        f"""
        SELECT symbol FROM fetch_manifest
        WHERE timespan = ? AND unit_key = ? AND status IN ('ok', 'empty')
          AND symbol IN ({placeholders})
        """,
        [timespan, unit_key, *symbols],
    ).fetchall()
    done = {r[0] for r in rows}
    return [s for s in symbols if s not in done]


def symbols_with_error_units(
    con: duckdb.DuckDBPyConnection,
    symbols: list[str],
    timespans: tuple[str, ...] = ("day", "minute"),
) -> list[str]:
    """Symbols in `symbols` with at least one 'error' fetch_manifest unit in
    any of `timespans` -- a partial backfill hole (e.g. one failed minute-month
    chunk out of an otherwise-successful onboarding) that a maintenance re-run
    should retry. Order-preserving."""
    if not symbols:
        return []
    placeholders = ",".join(["?"] * len(symbols))
    timespan_placeholders = ",".join(["?"] * len(timespans))
    rows = con.execute(
        f"""
        SELECT DISTINCT symbol FROM fetch_manifest
        WHERE status = 'error' AND timespan IN ({timespan_placeholders})
          AND symbol IN ({placeholders})
        """,
        [*timespans, *symbols],
    ).fetchall()
    hit = {r[0] for r in rows}
    return [s for s in symbols if s in hit]
