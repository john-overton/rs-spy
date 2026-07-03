"""M3.5: D1-cadence validation studies (algo-spec/08 §3.1-3.3, D1-adapted).

Runs the gate ablation, walk-away analysis, and RRS sensitivity sweep
against the full cached daily history for the curated universe, prints
summaries, and writes each study's raw output to reports/m35_studies/.

This is a checkpoint on a small, curated-universe, D1-cadence backtest --
not the full M5 system. Trade counts here are small (M3's baseline run
produced 8 trades); treat every conclusion as directional, not statistical
proof, until M7's full validation suite runs against the M5 system.
"""
import typer

from rs_spy.backtest.engine import BacktestConfig
from rs_spy.backtest.studies.ablation import run_gate_ablation
from rs_spy.backtest.studies.rrs_sensitivity import run_rrs_sensitivity
from rs_spy.backtest.studies.walk_away import run_walk_away
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(horizon_days: int = 20) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    all_bars = load_universe_daily_bars(con, universe.all_symbols)
    spy = all_bars[universe.primary_benchmark]
    qqq = all_bars[universe.secondary_benchmark]
    trade_bars = {s: all_bars[s] for s in universe.trade_symbols}
    sectors = {s.symbol: s.sector for s in universe.universe}
    config = BacktestConfig()

    out_dir = settings.reports_dir / "m35_studies"
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo("=== 3.1 Gate ablation ===")
    ablation = run_gate_ablation(trade_bars, spy, qqq, sectors, earnings_blackout, config)
    typer.echo(f"Trades per run: {ablation['run_trade_counts']}")
    typer.echo(f"Union of trades scored: {len(ablation['trades'])}")
    typer.echo(ablation["summary"].to_string(index=False))
    ablation["trades"].to_csv(out_dir / "ablation_trades.csv", index=False)
    ablation["summary"].to_csv(out_dir / "ablation_summary.csv", index=False)

    typer.echo("\n=== 3.2 Walk-away analysis ===")
    walk_away = run_walk_away(
        trade_bars, spy, qqq, sectors, earnings_blackout, config, horizon_days=horizon_days
    )
    signals = walk_away["signals"]
    realized = walk_away["realized_trades"]
    typer.echo(f"Entry signals (IDLE->QUALIFIED transitions): {len(signals)}")
    if not signals.empty:
        typer.echo(
            f"Signal MFE (R): mean={signals['mfe_r'].mean():.2f} "
            f"median={signals['mfe_r'].median():.2f} p90={signals['mfe_r'].quantile(0.9):.2f}"
        )
        typer.echo(
            f"Signal MAE (R): mean={signals['mae_r'].mean():.2f} "
            f"median={signals['mae_r'].median():.2f} p10={signals['mae_r'].quantile(0.1):.2f}"
        )
    if not realized.empty:
        typer.echo(
            f"Realized trade R: mean={realized['r_multiple'].mean():.2f} "
            f"median={realized['r_multiple'].median():.2f}"
        )
    signals.to_csv(out_dir / "walk_away_signals.csv", index=False)
    realized.to_csv(out_dir / "walk_away_realized_trades.csv", index=False)

    typer.echo("\n=== 3.3 RRS sensitivity sweep ===")
    sweep = run_rrs_sensitivity(trade_bars, spy, qqq, sectors, earnings_blackout, config)
    typer.echo(
        sweep[["window", "threshold", "basis", "n_trades", "win_rate", "profit_factor", "total_pnl"]]
        .to_string(index=False)
    )
    sweep.to_csv(out_dir / "rrs_sensitivity.csv", index=False)

    typer.echo(f"\nWrote study outputs to {out_dir}")


if __name__ == "__main__":
    app()
