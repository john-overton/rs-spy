"""Runs-store schema. Idempotent DDL applied on process start, mirroring
rs_spy.data.warehouse's _SCHEMA approach -- CREATE ... IF NOT EXISTS everywhere,
so init_schema() is safe to call on every connection. No Alembic: single
developer, single deployment, no migration history to preserve (revisit if a
breaking column change is ever needed).

Storage decisions:
  * trades      -- normalised (one row per trade). Trade counts are small and a
    UI may want to filter/aggregate across runs (by symbol, exit_reason, ...).
  * equity_curves -- ONE row per run holding the ~97k-point curve as a
    parquet-compressed BYTEA blob. The only access pattern is "whole curve for
    run X"; a normalised per-point table would pay millions of rows for zero
    query benefit.
"""
import psycopg

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
    CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS runs (
    run_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label        TEXT,
    engine       TEXT NOT NULL DEFAULT 'm5',
    status       run_status NOT NULL DEFAULT 'queued',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    config       JSONB NOT NULL,
    metrics      JSONB,
    funnel       JSONB,
    error        TEXT,
    git_sha      TEXT,
    host         TEXT,
    pid          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS trades (
    id           BIGSERIAL PRIMARY KEY,
    run_id       UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry_time   TIMESTAMPTZ NOT NULL,
    entry_price  DOUBLE PRECISION NOT NULL,
    exit_time    TIMESTAMPTZ NOT NULL,
    exit_price   DOUBLE PRECISION NOT NULL,
    shares       DOUBLE PRECISION NOT NULL,
    exit_reason  TEXT NOT NULL,
    pnl          DOUBLE PRECISION NOT NULL,
    r_multiple   DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_run ON trades (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);

CREATE TABLE IF NOT EXISTS equity_curves (
    run_id       UUID PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    n_points     INTEGER NOT NULL,
    format       TEXT NOT NULL DEFAULT 'parquet',
    data         BYTEA NOT NULL
);
"""


def init_schema(conn: psycopg.Connection) -> None:
    """Idempotently create runs/trades/equity_curves. Safe on every start."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA)
    conn.commit()
