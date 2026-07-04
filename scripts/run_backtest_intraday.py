"""M6: run the M5 event-driven backtest over the full cached minute-bar history
for the curated universe, print 08 §2 metrics, and write the trade log to
reports/m5_backtest/trades.csv."""
import json

import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics, metrics_by_direction
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(shorts: bool = False) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    all_m1 = load_universe_m1_bars(con, universe.all_symbols)
    all_m5 = load_universe_m5_bars(con, universe.all_symbols)
    all_d1 = load_universe_daily_bars(con, universe.all_symbols)

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    sectors = {s.symbol: s.sector for s in universe.universe}

    config = BacktestConfigM5(shorts_enabled=shorts)
    typer.echo(f"Running M5 backtest: {len(trade_symbols)} symbols, shorts_enabled={shorts}")
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
    trading_days = len(result.equity_curve.index.normalize().unique()) if result.equity_curve is not None else 0
    metrics = compute_metrics(trades, result.equity_curve, trading_days)

    typer.echo(f"\n{len(trades)} trades over {trading_days} trading days")
    for k, v in metrics.items():
        typer.echo(f"  {k}: {v}")

    if not trades.empty:
        typer.echo("\nBy direction:")
        for direction, m in metrics_by_direction(trades, config.starting_equity).items():
            typer.echo(f"  {direction}: {m}")
        typer.echo("\nExit reason breakdown:")
        typer.echo(trades["exit_reason"].value_counts().to_string())

    same_bar_stop_rate = (
        float((trades["entry_time"] == trades["exit_time"]).mean()) if not trades.empty else None
    )
    typer.echo("\nEntry funnel:")
    for k, v in result.funnel.items():
        if v:
            typer.echo(f"  {k}: {v}")
    typer.echo(f"  same_bar_stop_rate: {same_bar_stop_rate}")

    out_dir = settings.reports_dir / "m5_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False)
    with open(out_dir / "funnel.json", "w") as f:
        json.dump({**result.funnel, "same_bar_stop_rate": same_bar_stop_rate}, f, indent=2)
    if result.equity_curve is not None:
        result.equity_curve.to_csv(out_dir / "equity_curve.csv")
    typer.echo(f"\nWrote trade log to {out_dir / 'trades.csv'}")


if __name__ == "__main__":
    app()
