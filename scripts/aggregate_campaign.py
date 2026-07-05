"""Print + persist a campaign variant's pooled metrics (M10).

    python scripts/aggregate_campaign.py --tag jul05 --variant baseline
"""
from pathlib import Path

import typer

from rs_spy.backtest.aggregate import aggregate_campaign
from rs_spy.config import get_settings
from rs_spy.store.connection import connect_pg

app = typer.Typer()

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "m10_campaign"


@app.command()
def main(tag: str = typer.Option(...), variant: str = typer.Option(...)) -> None:
    conn = connect_pg(get_settings().database_url)
    try:
        out = aggregate_campaign(conn, tag, variant)
    finally:
        conn.close()
    typer.echo(f"{out['n_runs']} cohort runs pooled")
    for k, v in out["metrics"].items():
        typer.echo(f"  {k}: {v}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out["trades"].to_csv(OUT_DIR / f"{tag}-{variant}-trades.csv", index=False)
    typer.echo(f"trades -> {OUT_DIR / f'{tag}-{variant}-trades.csv'}")


if __name__ == "__main__":
    app()
