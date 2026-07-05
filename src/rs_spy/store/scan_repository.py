"""Plain-SQL repository for the scan tables (scan_runs, universe_snapshots,
screener_snapshots, onboarded_symbols). Style mirrors store/repository.py:
raw SQL, callers own the connection, writes commit.
"""
import math

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

_SNAPSHOT_COLS = (
    "symbol", "name", "exchange", "optionable", "last_close",
    "adv_shares", "adv_dollars", "n_bars", "passed", "first_fail",
)
_ONBOARDED_COLS = (
    "symbol", "onboarded_date", "source", "history_start",
    "n_daily_bars", "insufficient_history",
)


def _null_if_nan(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def save_scan(conn: psycopg.Connection, scan_date, evaluated: pd.DataFrame, funnel: dict) -> None:
    """Upsert the funnel row and REPLACE the date's snapshot rows (delete +
    COPY inside one transaction), so a re-run of the same night converges."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_runs (scan_date, funnel) VALUES (%s, %s) "
                "ON CONFLICT (scan_date) DO UPDATE SET funnel=excluded.funnel, captured_at=now()",
                (scan_date, Jsonb(funnel)),
            )
            cur.execute("DELETE FROM universe_snapshots WHERE scan_date=%s", (scan_date,))
            with cur.copy(
                "COPY universe_snapshots (scan_date, symbol, name, exchange, optionable, "
                "last_close, adv_shares, adv_dollars, n_bars, passed, first_fail) FROM STDIN"
            ) as copy:
                for sym, row in evaluated.iterrows():
                    copy.write_row(
                        (
                            scan_date, sym, row["name"], row["exchange"],
                            bool(row["optionable"]),
                            _null_if_nan(row["last_close"]),
                            _null_if_nan(row["adv_shares"]),
                            _null_if_nan(row["adv_dollars"]),
                            None if pd.isna(row["n_bars"]) else int(row["n_bars"]),
                            bool(row["passed"]),
                            _null_if_nan(row["first_fail"]),
                        )
                    )


def get_universe_snapshot(
    conn: psycopg.Connection, scan_date, passed_only: bool = False
) -> pd.DataFrame:
    extra = " AND passed" if passed_only else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_SNAPSHOT_COLS)} FROM universe_snapshots "
            f"WHERE scan_date=%s{extra} ORDER BY symbol",
            (scan_date,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=list(_SNAPSHOT_COLS))


def get_scan_funnel(conn: psycopg.Connection, scan_date) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT funnel FROM scan_runs WHERE scan_date=%s", (scan_date,))
        row = cur.fetchone()
    return row["funnel"] if row else None


def save_screener_snapshot(
    conn: psycopg.Connection, snapshot_date, endpoint: str, payload: dict
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO screener_snapshots (snapshot_date, endpoint, payload) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (snapshot_date, endpoint) "
            "DO UPDATE SET payload=excluded.payload, captured_at=now()",
            (snapshot_date, endpoint, Jsonb(payload)),
        )
    conn.commit()


def get_screener_snapshot(conn: psycopg.Connection, snapshot_date, endpoint: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM screener_snapshots WHERE snapshot_date=%s AND endpoint=%s",
            (snapshot_date, endpoint),
        )
        row = cur.fetchone()
    return row["payload"] if row else None


def record_onboarded(
    conn: psycopg.Connection,
    symbol: str,
    onboarded_date,
    *,
    source: str,
    history_start,
    n_daily_bars: int,
    insufficient_history: bool,
) -> bool:
    """First insert wins (a repeat most-actives appearance must not re-onboard).
    Returns True only when this call inserted the row."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO onboarded_symbols "
            "(symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) DO NOTHING",
            (symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history),
        )
        inserted = cur.rowcount == 1
    conn.commit()
    return inserted


def update_onboarded(conn: psycopg.Connection, outcome) -> None:
    """Update n_daily_bars/history_start/insufficient_history for an existing
    onboarded_symbols row from a fresh `OnboardingOutcome` (the nightly
    maintenance pass repairing an insufficient-history symbol that has since
    matured, or a partial-backfill hole). `source`/`onboarded_date` are left
    untouched -- this call never re-attributes how/when a symbol first
    entered. A no-op (0 rows) if `outcome.symbol` isn't onboarded yet."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE onboarded_symbols SET n_daily_bars=%s, history_start=%s, "
            "insufficient_history=%s WHERE symbol=%s",
            (
                outcome.n_daily_bars,
                outcome.history_start,
                outcome.insufficient_history,
                outcome.symbol,
            ),
        )
    conn.commit()


def list_onboarded(conn: psycopg.Connection) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_ONBOARDED_COLS)} FROM onboarded_symbols "
            f"ORDER BY onboarded_date, symbol"
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=list(_ONBOARDED_COLS))
