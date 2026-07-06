"""M11 Phase 1 driver: cycle-oscillator skill study on real SPY data.

    python scripts/run_oscillator_study.py train
    python scripts/run_oscillator_study.py holdout --spec close-12-26-9

train: sweeps the 24-candidate grid on 2021->2024, writes
reports/tuning/oscillator_skill_train.csv, prints the leaderboard, the
winner's per-state practice table, the incumbent bias engine scored with the
same metric, and the winner's LONG-trigger composition table.

holdout: SINGLE-SHOT gate (spec 2026-07-05-cycle-oscillator-design.md).
Accepts exactly one --spec name; evaluates it + the incumbent on 2025->2026;
prints PASS/FAIL per pre-committed check; writes
reports/tuning/oscillator_skill_holdout.csv. Running it repeatedly with
different specs burns the holdout -- don't.
"""
from pathlib import Path

import pandas as pd
import typer

from rs_spy.backtest.studies.oscillator_skill_m5 import (
    candidate_grid,
    cross_skill_table,
    holdout_verdict,
    incumbent_skill,
    run_train_sweep,
    separation_scores,
    split_train_holdout,
    state_skill_table,
    trigger_composition_table,
)
from rs_spy.bias.engine import bias_series
from rs_spy.config import get_settings
from rs_spy.data.loader import load_daily_bars, load_m5_bars, load_minute_bars
from rs_spy.data.warehouse import connect
from rs_spy.indicators.cycle_oscillator import (
    compute_oscillator,
    oscillator_crosses,
    oscillator_states,
)

app = typer.Typer()
OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "tuning"


def _load_frames():
    settings = get_settings()
    con = connect(settings.resolved_warehouse_path(), read_only=True)
    try:
        spy_m1 = load_minute_bars(con, "SPY")
        spy_m5 = load_m5_bars(con, "SPY")
        spy_d1 = load_daily_bars(con, "SPY")
        qqq_m1 = load_minute_bars(con, "QQQ")
        qqq_m5 = load_m5_bars(con, "QQQ")
    finally:
        con.close()
    return spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5


def _print_table(title: str, df: pd.DataFrame) -> None:
    typer.echo(f"\n== {title} ==")
    typer.echo(df.to_string(index=False))


@app.command()
def train() -> None:
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _load_frames()
    results, winner = run_train_sweep(spy_m5, candidate_grid())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUT_DIR / "oscillator_skill_train.csv", index=False)
    _print_table(
        "leaderboard (train)",
        results.sort_values("sep_24", ascending=False).head(10),
    )
    if winner is None:
        typer.echo("NO ELIGIBLE CANDIDATE -- study ends here (null result).")
        raise typer.Exit(code=1)
    typer.echo(f"\nWINNER (train): {winner.name}")

    train_m5, _ = split_train_holdout(spy_m5)
    osc = compute_oscillator(train_m5, winner)
    states = oscillator_states(osc)
    _print_table("winner per-state (train)", state_skill_table(states, train_m5["close"]))
    _print_table("winner crosses (train)",
                 cross_skill_table(oscillator_crosses(osc), train_m5["close"]))

    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    train_bias, _ = split_train_holdout(bias_df)
    inc_table, inc_scores = incumbent_skill(train_bias["bias"], train_m5["close"])
    _print_table("incumbent buckets (train, same metric)", inc_table)
    typer.echo(f"incumbent separation (train): {inc_scores}")

    comp = trigger_composition_table(train_bias["trigger"], states, train_m5["close"])
    _print_table("LONG-trigger composition by winner state (train)", comp)
    typer.echo(
        "\nNext: python scripts/run_oscillator_study.py holdout --spec " + winner.name
    )


@app.command()
def holdout(spec: str = typer.Option(...)) -> None:
    grid = {s.name: s for s in candidate_grid()}
    if spec not in grid:
        raise typer.BadParameter(
            f"unknown spec {spec!r}; must be one of the 24 grid names"
        )
    chosen = grid[spec]

    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _load_frames()
    train_m5, holdout_m5 = split_train_holdout(spy_m5)

    # train sep_24 for the sign-consistency check (recomputed, train data only)
    t_osc = compute_oscillator(train_m5, chosen)
    t_scores = separation_scores(
        state_skill_table(oscillator_states(t_osc), train_m5["close"])
    )

    osc = compute_oscillator(holdout_m5, chosen)
    states = oscillator_states(osc)
    table = state_skill_table(states, holdout_m5["close"])
    scores = separation_scores(table)

    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    _, holdout_bias = split_train_holdout(bias_df)
    inc_table, inc_scores = incumbent_skill(holdout_bias["bias"], holdout_m5["close"])

    verdict = holdout_verdict(scores, inc_scores, t_scores["sep_24"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = pd.concat(
        [table.assign(who=spec), inc_table.assign(who="incumbent")], ignore_index=True
    )
    out.to_csv(OUT_DIR / "oscillator_skill_holdout.csv", index=False)

    _print_table(f"{spec} per-state (holdout)", table)
    _print_table("incumbent (holdout, same metric)", inc_table)
    comp = trigger_composition_table(holdout_bias["trigger"], states, holdout_m5["close"])
    _print_table("LONG-trigger composition (holdout)", comp)
    typer.echo(f"\nwinner scores:    {scores}")
    typer.echo(f"incumbent scores: {inc_scores}")
    typer.echo(f"train sep_24:     {t_scores['sep_24']}")
    for check, ok in verdict["checks"].items():
        typer.echo(f"  {'PASS' if ok else 'FAIL'}  {check}")
    typer.echo(f"\nVERDICT: {'PASS -- Phase 2 unlocked' if verdict['pass'] else 'FAIL -- null result, keep current engine'}")
    raise typer.Exit(code=0 if verdict["pass"] else 1)


if __name__ == "__main__":
    app()
