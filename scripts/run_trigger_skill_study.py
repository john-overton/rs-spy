"""M7.5 Phase 0 (tuning-matrix cell D1): run the trigger forward-return study
against the real cached warehouse. Only needs SPY/QQQ bars (no universe, no
backtest) -- runs in a minute or two, not 15-20. Writes
reports/tuning/trigger_skill.csv."""
import typer

from rs_spy.backtest.studies.trigger_skill_m5 import run_trigger_skill_m5
from rs_spy.config import get_settings
from rs_spy.data.loader import load_universe_daily_bars, load_universe_m1_bars, load_universe_m5_bars
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_universe

app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    universe = load_universe(settings.config_dir / "universe.yaml")
    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    con = connect(settings.resolved_warehouse_path())

    benchmarks = [spy, qqq]
    all_m1 = load_universe_m1_bars(con, benchmarks)
    all_m5 = load_universe_m5_bars(con, benchmarks)
    all_d1 = load_universe_daily_bars(con, benchmarks)

    typer.echo("Computing bias series + trigger forward returns (SPY/QQQ only)...")
    table = run_trigger_skill_m5(
        all_m1[spy], all_m5[spy], all_d1[spy], all_m1[qqq], all_m5[qqq],
    )
    typer.echo(table.to_string(index=False))

    out_dir = settings.reports_dir / "tuning"
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "trigger_skill.csv", index=False)
    typer.echo(f"\nWrote {out_dir / 'trigger_skill.csv'}")


if __name__ == "__main__":
    app()
