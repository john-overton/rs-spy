"""Plain-SQL repository over the runs-store (rs_spy.store.schema).

Style mirrors rs_spy.data.warehouse / manifest: raw SQL against a psycopg
connection, no ORM. Callers own the connection; write functions commit.
"""
import uuid

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from rs_spy.backtest.engine_m5 import BacktestConfigM5, BacktestResultM5
from rs_spy.store.serialize import (
    bytes_to_equity,
    config_from_jsonb,
    config_to_jsonb,
    equity_to_bytes,
)

_TRADE_COLS = (
    "symbol", "direction", "entry_time", "entry_price", "exit_time",
    "exit_price", "shares", "exit_reason", "pnl", "r_multiple",
)


def create_run(
    conn: psycopg.Connection,
    config: BacktestConfigM5,
    *,
    label: str | None = None,
    engine: str = "m5",
    git_sha: str | None = None,
    status: str = "queued",
) -> uuid.UUID:
    """Insert a new run row and return its generated run_id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (label, engine, status, config, git_sha) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING run_id",
            (label, engine, status, Jsonb(config_to_jsonb(config)), git_sha),
        )
        run_id = cur.fetchone()["run_id"]
    conn.commit()
    return run_id


def mark_running(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    pid: int | None = None,
    host: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status='running', started_at=now(), pid=%s, host=%s "
            "WHERE run_id=%s",
            (pid, host, run_id),
        )
    conn.commit()


def save_result(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    result: BacktestResultM5,
    metrics: dict,
    *,
    same_bar_stop_rate: float | None = None,
) -> None:
    """Persist a completed run atomically: mark succeeded + metrics/funnel,
    bulk-insert trades (order preserved via seq), store the equity blob.

    `metrics` must already be JSONB-safe (see serialize.sanitize_metrics).
    """
    funnel = dict(result.funnel) if result.funnel else {}
    if same_bar_stop_rate is not None:
        funnel = {**funnel, "same_bar_stop_rate": same_bar_stop_rate}

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='succeeded', finished_at=now(), "
                "metrics=%s, funnel=%s WHERE run_id=%s",
                (Jsonb(metrics), Jsonb(funnel), run_id),
            )
            if result.trades:
                with cur.copy(
                    "COPY trades (run_id, seq, symbol, direction, entry_time, "
                    "entry_price, exit_time, exit_price, shares, exit_reason, "
                    "pnl, r_multiple) FROM STDIN"
                ) as copy:
                    for seq, t in enumerate(result.trades):
                        copy.write_row(
                            (run_id, seq, t.symbol, t.direction, t.entry_time,
                             t.entry_price, t.exit_time, t.exit_price, t.shares,
                             t.exit_reason, t.pnl, t.r_multiple)
                        )
            eq = equity_to_bytes(result.equity_curve)
            if eq is not None:
                data, n_points = eq
                cur.execute(
                    "INSERT INTO equity_curves (run_id, n_points, data) "
                    "VALUES (%s, %s, %s)",
                    (run_id, n_points, data),
                )


def mark_failed(conn: psycopg.Connection, run_id: uuid.UUID, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status='failed', finished_at=now(), error=%s "
            "WHERE run_id=%s",
            (error, run_id),
        )
    conn.commit()


def get_run(conn: psycopg.Connection, run_id: uuid.UUID) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        return cur.fetchone()


def list_runs(
    conn: psycopg.Connection,
    *,
    status: str | None = None,
    engine: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Recent runs first. Optional status/engine filters. Excludes the large
    JSONB columns is unnecessary here -- runs rows are small; the equity blob
    lives in its own table and is never dragged along."""
    clauses, params = [], []
    if status is not None:
        clauses.append("status=%s")
        params.append(status)
    if engine is not None:
        clauses.append("engine=%s")
        params.append(engine)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params,
        )
        return cur.fetchall()


def get_trades(conn: psycopg.Connection, run_id: uuid.UUID) -> pd.DataFrame:
    """Trade log for a run as a DataFrame, ordered by original sequence.
    Columns match TradeM5 (BacktestResultM5.trades_df())."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_TRADE_COLS)} FROM trades "
            "WHERE run_id=%s ORDER BY seq",
            (run_id,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=list(_TRADE_COLS))


def get_equity(conn: psycopg.Connection, run_id: uuid.UUID) -> pd.Series | None:
    with conn.cursor() as cur:
        cur.execute("SELECT data FROM equity_curves WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return bytes_to_equity(bytes(row["data"]))


def get_config(conn: psycopg.Connection, run_id: uuid.UUID) -> BacktestConfigM5:
    """Reconstruct a run's BacktestConfigM5 (for re-running/cloning a run)."""
    with conn.cursor() as cur:
        cur.execute("SELECT config FROM runs WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"no run {run_id}")
    return config_from_jsonb(row["config"])
