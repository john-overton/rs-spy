"""Run one M5 backtest and record it to the Postgres runs-store.

Crash-safe: any exception marks the run 'failed' (with a traceback) before
re-raising, so the stored status always reflects reality for anything short of a
hard kill. A hard SIGKILL/OOM leaves the run stuck in 'running' -- the stored
pid/host/started_at let a UI reaper flag stale runs (see launch.py docs).

Opens the DuckDB warehouse READ-ONLY, so multiple jobs (each its own process,
its own read-only con, its own Postgres connection) read the same warehouse
concurrently. Memory, not I/O, is the real limit on how many to run at once.
"""
import os
import socket
import subprocess
import traceback
import uuid

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics
from rs_spy.config import get_settings
from rs_spy.data.loader import (
    load_universe_daily_bars,
    load_universe_m1_bars,
    load_universe_m5_bars,
)
from rs_spy.data.warehouse import connect
from rs_spy.store import repository as repo
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema
from rs_spy.store.serialize import sanitize_metrics
from rs_spy.universe import load_earnings_blackout, load_universe


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _trade_symbols(universe, config: BacktestConfigM5) -> list[str]:
    """Curated trade symbols plus config.extra_symbols (M9 onboarding),
    order-preserving, minus anything already curated or a benchmark."""
    known = set(universe.all_symbols)
    extra = [s for s in config.extra_symbols if s not in known]
    return [*universe.trade_symbols, *extra]


def run_job(
    run_id: uuid.UUID | None = None,
    config: BacktestConfigM5 | None = None,
    *,
    label: str | None = None,
    database_url: str | None = None,
) -> uuid.UUID:
    """Execute a backtest run and persist it. Provide `run_id` to run an existing
    queued run (created by the UI), or `config` to create one here (standalone
    CLI). Returns the run_id. Marks the run failed + re-raises on any error."""
    if run_id is None and config is None:
        raise ValueError("run_job requires either run_id or config")

    conn = connect_pg(database_url)
    try:
        init_schema(conn)
        if run_id is None:
            run_id = repo.create_run(conn, config, label=label, git_sha=_git_sha())
        else:
            config = repo.get_config(conn, run_id)

        repo.mark_running(conn, run_id, pid=os.getpid(), host=socket.gethostname())
        try:
            result, metrics, same_bar = _execute_backtest(config)
            repo.save_result(conn, run_id, result, metrics, same_bar_stop_rate=same_bar)
        except BaseException as e:  # noqa: BLE001 -- record failure for KeyboardInterrupt/SystemExit too
            repo.mark_failed(conn, run_id, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            raise
    finally:
        conn.close()
    return run_id


def _execute_backtest(config: BacktestConfigM5):
    """Load bars (warehouse read-only) and run the M5 backtest. Wiring mirrors
    scripts/run_backtest_intraday.py."""
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")

    warehouse_path = settings.resolved_warehouse_path()
    if not warehouse_path.exists():
        raise FileNotFoundError(
            f"warehouse not found at {warehouse_path}; run a backfill script first"
        )
    con = connect(warehouse_path, read_only=True)
    try:
        trade_symbols = _trade_symbols(universe, config)
        load_symbols = list(dict.fromkeys([*universe.all_symbols, *trade_symbols]))
        all_m1 = load_universe_m1_bars(con, load_symbols)
        all_m5 = load_universe_m5_bars(con, load_symbols)
        all_d1 = load_universe_daily_bars(con, load_symbols)
    finally:
        con.close()

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    sectors = {s.symbol: s.sector for s in universe.universe}
    for sym in trade_symbols:
        sectors.setdefault(sym, "UNKNOWN")  # onboarded symbols have no GICS mapping (v1)
    # Known limitation: EVERY onboarded symbol shares the single "UNKNOWN" sector
    # bucket, so the engine's max_per_sector cap (default 2, see
    # BacktestConfigM5) throttles onboarded names as a group per tradeable-list
    # rebuild -- e.g. at most 2 onboarded symbols can ever be listed/held
    # simultaneously, regardless of how many pass the selection gates. Documented,
    # not changed (see IMPLEMENTATION.md).

    result = run_m5_backtest(
        universe_m1={s: all_m1[s] for s in trade_symbols},
        universe_m5={s: all_m5[s] for s in trade_symbols},
        universe_d1={s: all_d1[s] for s in trade_symbols},
        spy_m1=all_m1[spy], spy_m5=all_m5[spy], spy_d1=all_d1[spy],
        qqq_m1=all_m1[qqq], qqq_m5=all_m5[qqq],
        sectors=sectors,
        earnings_blackout=earnings_blackout,
        config=config,
    )

    trades = result.trades_df()
    trading_days = (
        len(result.equity_curve.index.normalize().unique())
        if result.equity_curve is not None else 0
    )
    metrics = sanitize_metrics(compute_metrics(trades, result.equity_curve, trading_days))
    same_bar = (
        float((trades["entry_time"] == trades["exit_time"]).mean())
        if not trades.empty else None
    )
    return result, metrics, same_bar
