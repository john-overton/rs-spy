"""Postgres runs-store: persists backtest runs (config, status, metrics, funnel),
their trade logs, and equity curves so multiple backtests can run concurrently
and a UI can list/compare/fire runs.

Market-data bars stay in DuckDB (rs_spy.data.warehouse); this package holds only
results. Plain-SQL psycopg3 repository, mirroring the warehouse module's style
(raw SQL, idempotent schema) -- no ORM, no migration tool. See docker-compose.yml.
"""
from rs_spy.store.connection import connect_pg
from rs_spy.store.repository import (
    create_run,
    get_config,
    get_equity,
    get_run,
    get_trades,
    list_runs,
    mark_failed,
    mark_running,
    save_result,
)
from rs_spy.store.schema import init_schema

__all__ = [
    "connect_pg",
    "init_schema",
    "create_run",
    "mark_running",
    "save_result",
    "mark_failed",
    "get_run",
    "list_runs",
    "get_trades",
    "get_equity",
    "get_config",
]
