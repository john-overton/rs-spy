"""M3: run the D1 walking-skeleton backtest over the full cached daily
history for the curated universe, print 08 §2 metrics, and write the trade
log to reports/d1_backtest/trades.csv."""
import typer

from rs_spy.backtest.engine import BacktestConfig, run_d1_backtest
from rs_spy.backtest.metrics import compute_metrics, metrics_by_direction
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(shorts: bool = False) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path(), read_only=True)

    all_bars = load_universe_daily_bars(con, universe.all_symbols)
    spy = all_bars[universe.primary_benchmark]
    qqq = all_bars[universe.secondary_benchmark]
    trade_bars = {s: all_bars[s] for s in universe.trade_symbols}
    sectors = {s.symbol: s.sector for s in universe.universe}

    config = BacktestConfig(shorts_enabled=shorts)
    typer.echo(f"Running D1 backtest: {len(trade_bars)} symbols, shorts_enabled={shorts}")
    result = run_d1_backtest(trade_bars, spy, qqq, sectors, earnings_blackout, config)

    trades = result.trades_df()
    trading_days = len(result.equity_curve) if result.equity_curve is not None else 0
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

    out_dir = settings.reports_dir / "d1_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False)
    if result.equity_curve is not None:
        result.equity_curve.to_csv(out_dir / "equity_curve.csv")
    typer.echo(f"\nWrote trade log to {out_dir / 'trades.csv'}")


if __name__ == "__main__":
    app()
