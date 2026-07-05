"""M7: full validation study suite (algo-spec 08 §3), M5 cadence. Adds an M5
sibling to scripts/run_validation_studies_m35.py's D1-cadence suite (kept
unchanged -- that precedent is documented in IMPLEMENTATION.md's M3.5
section) -- the same relationship run_backtest_intraday.py has to
run_backtest_d1.py.

**Runtime**: this is SLOW. One shared baseline backtest + 6 gate-ablation
re-runs + 9 RRS-sensitivity re-runs = ~16 run_m5_backtest invocations (the baseline shares its own _prepare_m5 via prepared=),
each on the order of 15-20 minutes for the full curated universe (the M5
precompute layer's non-vectorized indicator loops dominate -- see this
repo's README for the identical note on run_backtest_intraday.py). Expect
several hours for the full suite. The bias-confusion (§3.4) and
time-of-day/regime (§3.5) studies are comparatively instant (no extra
backtest runs).
"""
import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5, run_m5_backtest
from rs_spy.backtest.studies.ablation_m5 import run_gate_ablation_m5
from rs_spy.backtest.studies.bias_confusion_m5 import run_bias_confusion_m5
from rs_spy.backtest.studies.rrs_sensitivity_m5 import run_rrs_sensitivity_m5
from rs_spy.backtest.studies.time_of_day_m5 import run_time_of_day_regime_slice_m5
from rs_spy.backtest.studies.walk_away_m5 import run_walk_away_m5
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main(horizon_bars_walk_away: int = 78, horizon_bars_bias: int = 12) -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path(), read_only=True)

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    load_syms = list(dict.fromkeys([spy, qqq, *trade_symbols]))

    typer.echo(f"Loading real cached data for {len(load_syms)} symbols (full window)...")
    all_m1 = load_universe_m1_bars(con, load_syms)
    all_m5 = load_universe_m5_bars(con, load_syms)
    all_d1 = load_universe_daily_bars(con, load_syms)

    trade_m1 = {s: all_m1[s] for s in trade_symbols}
    trade_m5 = {s: all_m5[s] for s in trade_symbols}
    trade_d1 = {s: all_d1[s] for s in trade_symbols}
    sectors = {s.symbol: s.sector for s in universe.universe}

    base_config = BacktestConfigM5(shorts_enabled=True)
    out_dir = settings.reports_dir / "m7_studies"
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo("\n=== Baseline M5 backtest (shared by walk-away, ablation scoring, time-of-day) ===")
    baseline_prepared = _prepare_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
    )
    baseline_result = run_m5_backtest(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
        prepared=baseline_prepared,
    )
    baseline_trades = baseline_result.trades_df()
    typer.echo(f"Baseline trades: {len(baseline_result.trades)}")
    baseline_trades.to_csv(out_dir / "baseline_trades.csv", index=False)

    typer.echo("\n=== 3.1 Gate ablation (M5, 6 additional runs) ===")
    ablation = run_gate_ablation_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
        baseline_prepared, baseline_result,
    )
    typer.echo(f"Trades per run: {ablation['run_trade_counts']}")
    typer.echo("LONG summary:\n" + ablation["summary_long"].to_string(index=False))
    typer.echo("SHORT summary:\n" + ablation["summary_short"].to_string(index=False))
    ablation["trades"].to_csv(out_dir / "ablation_trades.csv", index=False)
    ablation["summary_long"].to_csv(out_dir / "ablation_summary_long.csv", index=False)
    ablation["summary_short"].to_csv(out_dir / "ablation_summary_short.csv", index=False)

    typer.echo("\n=== 3.2 Walk-away analysis (M5, reuses baseline run) ===")
    walk_away = run_walk_away_m5(baseline_prepared, baseline_trades, base_config, horizon_bars=horizon_bars_walk_away)
    signals = walk_away["signals"]
    typer.echo(f"Entry signals (IDLE->QUALIFIED): {len(signals)}")
    if not signals.empty:
        for direction in ("LONG", "SHORT"):
            sub = signals[signals["direction"] == direction]
            if sub.empty:
                continue
            typer.echo(f"  {direction} MFE (R): mean={sub['mfe_r'].mean():.2f} median={sub['mfe_r'].median():.2f}")
            typer.echo(f"  {direction} MAE (R): mean={sub['mae_r'].mean():.2f} median={sub['mae_r'].median():.2f}")
    if not baseline_trades.empty:
        typer.echo(
            f"Realized trade R: mean={baseline_trades['r_multiple'].mean():.2f} "
            f"median={baseline_trades['r_multiple'].median():.2f}"
        )
    signals.to_csv(out_dir / "walk_away_signals.csv", index=False)

    typer.echo("\n=== 3.3 RRS sensitivity sweep (M5, 9 runs) ===")
    sweep = run_rrs_sensitivity_m5(
        trade_m1, trade_m5, trade_d1, all_m1[spy], all_m5[spy], all_d1[spy],
        all_m1[qqq], all_m5[qqq], sectors, earnings_blackout, base_config,
    )
    typer.echo(sweep.to_string(index=False))
    sweep.to_csv(out_dir / "rrs_sensitivity.csv", index=False)

    typer.echo("\n=== 3.4 Bias-engine confusion matrix ===")
    confusion = run_bias_confusion_m5(
        all_m1[spy], all_m5[spy], all_d1[spy], all_m1[qqq], all_m5[qqq],
        horizon_bars=horizon_bars_bias,
    )
    typer.echo(confusion["contingency"].to_string(index=False))
    typer.echo(f"Hit rates: {confusion['hit_rates']}")
    confusion["contingency"].to_csv(out_dir / "bias_confusion.csv", index=False)

    typer.echo("\n=== 3.5 Time-of-day / regime slicing ===")
    tod = run_time_of_day_regime_slice_m5(baseline_trades, baseline_prepared.regime_d1_m5)
    typer.echo(tod.to_string(index=False))
    tod.to_csv(out_dir / "time_of_day_regime.csv", index=False)

    typer.echo(f"\nWrote all study outputs to {out_dir}")


if __name__ == "__main__":
    app()
