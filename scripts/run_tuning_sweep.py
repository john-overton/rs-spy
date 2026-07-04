"""M7.5 tuning-campaign sweep driver (docs/tuning/m7.5-tuning-matrix.md Rounds 2-3).

Runs one RRS-window group of the sweep grid: for each rrs_m5 threshold, build a
single PreparedM5 (the expensive ~15-min precompute), then run every stop-mult
variant against it via run_m5_backtest(prepared=...) -- stop_atr_mult is
event-loop-only per that function's documented contract, so the stop sweep is
nearly free once a threshold cell is prepared. Thresholds and windows are
prepare-baked, hence one precompute per (window, threshold) cell.

Each run writes reports/tuning/<run_id>/{config.json,trades.csv,funnel.json}
and appends one row to reports/tuning/sweep_results.csv (the raw feed for
docs/tuning/ledger.csv). run_id convention: r23-w<window>-t<thr*10>-s<mult*10>,
e.g. r23-w18-t05-s15.

Usage: python scripts/run_tuning_sweep.py --window 18
"""
import csv
import gc
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()

RESULT_COLUMNS = [
    "run_id", "window", "rrs_m5_threshold", "stop_atr_mult", "shorts_enabled",
    "n_trades", "n_long", "n_short", "win_rate", "profit_factor", "avg_r",
    "max_drawdown_pct", "total_pnl", "same_bar_stop_rate",
    "hard_stops", "trail_stops", "profit_takes", "other_exits",
    "qualified_long", "dip_armed_long", "trigger_coincidences_long",
    "killed_by_bias_hold_long", "trigger_bypass_long", "killed_by_quality_long",
    "orders_filled_long", "orders_filled_short", "prepare_seconds",
]


def _fmt(x: float) -> str:
    return str(x).replace(".", "").rstrip("0") or "0"


def _append_result(path: Path, row: dict) -> None:
    exists = path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


@app.command()
def main(
    window: int = typer.Option(..., help="rrs_m5_window for this sweep group"),
    thresholds: str = typer.Option("1.0,0.5,0.0", help="comma-separated rrs_m5 long thresholds (short side mirrored negative)"),
    stop_mults: str = typer.Option("1.0,1.5,2.0", help="comma-separated stop_atr_mult values"),
    shorts: bool = typer.Option(True, help="shorts_enabled (True matches the M7 study-suite convention)"),
) -> None:
    thr_list = [float(t) for t in thresholds.split(",")]
    mult_list = [float(m) for m in stop_mults.split(",")]

    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    sectors = {s.symbol: s.sector for s in universe.universe}
    load_syms = list(dict.fromkeys([spy, qqq, *trade_symbols]))

    typer.echo(f"[w{window}] loading bars for {len(load_syms)} symbols...")
    t0 = time.time()
    all_m1 = load_universe_m1_bars(con, load_syms)
    all_m5 = load_universe_m5_bars(con, load_syms)
    all_d1 = load_universe_daily_bars(con, load_syms)
    trade_m1 = {s: all_m1[s] for s in trade_symbols}
    trade_m5 = {s: all_m5[s] for s in trade_symbols}
    trade_d1 = {s: all_d1[s] for s in trade_symbols}
    typer.echo(f"[w{window}] loaded in {time.time() - t0:.0f}s")

    out_root = settings.reports_dir / "tuning"
    out_root.mkdir(parents=True, exist_ok=True)
    results_csv = out_root / "sweep_results.csv"

    prepared = None
    for thr in thr_list:
        # Release the previous threshold's PreparedM5 BEFORE building the next
        # one -- holding both across the _prepare_m5 call doubles peak memory
        # and has OOM-killed multi-threshold sweep runs on this machine.
        del prepared
        gc.collect()
        base_config = BacktestConfigM5(
            shorts_enabled=shorts,
            rrs_m5_window=window,
            rrs_m5_threshold_long=thr,
            rrs_m5_threshold_short=-thr,
        )
        typer.echo(f"[w{window} t{thr}] preparing (~15 min)...")
        t0 = time.time()
        prepared = _prepare_m5(
            trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
            all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
        )
        prepare_seconds = round(time.time() - t0)
        typer.echo(f"[w{window} t{thr}] prepared in {prepare_seconds}s")

        for mult in mult_list:
            config = replace(base_config, stop_atr_mult=mult)
            run_id = f"r23-w{window}-t{_fmt(thr)}-s{_fmt(mult)}"
            result = run_m5_backtest(
                trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
                all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, config,
                prepared=prepared,
            )
            trades = result.trades_df()
            trading_days = len(result.equity_curve.index.normalize().unique())
            metrics = compute_metrics(trades, result.equity_curve, trading_days)
            n = len(trades)
            same_bar = float((trades["entry_time"] == trades["exit_time"]).mean()) if n else None
            avg_r = float(trades["r_multiple"].mean()) if n else None
            n_long = int((trades["direction"] == "LONG").sum()) if n else 0
            n_short = n - n_long
            exit_counts = trades["exit_reason"].value_counts().to_dict() if n else {}
            hard_stops = int(exit_counts.pop("hard_stop", 0))
            trail_stops = int(exit_counts.pop("trail_stop", 0))
            profit_takes = int(exit_counts.pop("profit_take", 0))

            run_dir = out_root / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            cfg = asdict(config)
            cfg["disabled_gates"] = sorted(cfg["disabled_gates"])
            with open(run_dir / "config.json", "w") as f:
                json.dump(cfg, f, indent=2)
            trades.to_csv(run_dir / "trades.csv", index=False)
            with open(run_dir / "funnel.json", "w") as f:
                json.dump({**result.funnel, "same_bar_stop_rate": same_bar}, f, indent=2)

            fn = result.funnel
            _append_result(results_csv, {
                "run_id": run_id, "window": window, "rrs_m5_threshold": thr,
                "stop_atr_mult": mult, "shorts_enabled": shorts,
                "n_trades": n, "n_long": n_long, "n_short": n_short,
                "win_rate": metrics.get("win_rate"), "profit_factor": metrics.get("profit_factor"),
                "avg_r": avg_r, "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_pnl": metrics.get("total_pnl"), "same_bar_stop_rate": same_bar,
                "hard_stops": hard_stops, "trail_stops": trail_stops, "profit_takes": profit_takes,
                "other_exits": int(sum(exit_counts.values())),
                "qualified_long": fn["long_qualified_signals"],
                "dip_armed_long": fn["long_dip_armed"],
                "trigger_coincidences_long": fn["long_trigger_coincidences"],
                "killed_by_bias_hold_long": fn["long_trigger_killed_by_bias_hold"],
                "trigger_bypass_long": fn["long_trigger_bypass"],
                "killed_by_quality_long": fn["long_eval_killed_by_quality"],
                "orders_filled_long": fn["long_orders_filled"],
                "orders_filled_short": fn["short_orders_filled"],
                "prepare_seconds": prepare_seconds,
            })
            typer.echo(
                f"[{run_id}] trades={n} (L{n_long}/S{n_short}) pf={metrics.get('profit_factor')} "
                f"pnl={metrics.get('total_pnl')} same_bar={same_bar} "
                f"stops={hard_stops} trails={trail_stops} takes={profit_takes} "
                f"funnel: coinc={fn['long_trigger_coincidences']} biaskill={fn['long_trigger_killed_by_bias_hold']} "
                f"qualkill={fn['long_eval_killed_by_quality']} armed={fn['long_dip_armed']}"
            )

    typer.echo(f"[w{window}] group complete -> {results_csv}")


if __name__ == "__main__":
    app()
