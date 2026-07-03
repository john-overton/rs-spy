"""M7 pre-work: full-universe gate-pass-rate / watchlist-state audit
(src/rs_spy/backtest/studies/gate_audit_m5.py). Confirms whether the
confluence-rarity finding from M6's 7-symbol diagnostic sample generalizes
across the full curated universe, after the gate_adv M5-cadence fix.

Slow: runs the full per-symbol M5 feature computation across the whole
universe -- same cost as scripts/run_backtest_intraday.py (tens of minutes,
not seconds).
"""
import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.backtest.studies.gate_audit_m5 import run_gate_pass_audit
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_earnings_blackout, load_universe

app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    earnings_blackout = load_earnings_blackout(settings.config_dir / "reference_overrides.yaml")
    con = connect(settings.resolved_warehouse_path())

    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    load_syms = list(dict.fromkeys([spy, qqq, *trade_symbols]))

    typer.echo(f"Loading real cached data for {len(load_syms)} symbols (full window)...")
    all_m1 = load_universe_m1_bars(con, load_syms)
    all_m5 = load_universe_m5_bars(con, load_syms)
    all_d1 = load_universe_daily_bars(con, load_syms)

    config = BacktestConfigM5()
    typer.echo(f"Running gate-pass audit: {len(trade_symbols)} symbols...")
    result = run_gate_pass_audit(
        universe_m1={s: all_m1[s] for s in trade_symbols},
        universe_m5={s: all_m5[s] for s in trade_symbols},
        universe_d1={s: all_d1[s] for s in trade_symbols},
        spy_m1=all_m1[spy], spy_m5=all_m5[spy], spy_d1=all_d1[spy],
        qqq_m5=all_m5[qqq],
        earnings_blackout=earnings_blackout,
        config=config,
    )

    out_dir = settings.reports_dir / "gate_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    result["per_gate"].to_csv(out_dir / "per_gate_pass_rates.csv", index=False)
    result["watchlist"].to_csv(out_dir / "watchlist_state_reach.csv", index=False)

    typer.echo("\n=== Summary across universe ===")
    for k, v in result["summary"].items():
        typer.echo(f"  {k}: {v}")
    typer.echo(f"\nWrote per-symbol detail to {out_dir}")


if __name__ == "__main__":
    app()
