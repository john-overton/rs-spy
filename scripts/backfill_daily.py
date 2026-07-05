"""M1: backfill daily bars for the curated universe (config/universe.yaml).

Safe to interrupt (Ctrl-C) and re-run -- see rs_spy.data.ingest / manifest for
the resumability mechanism.
"""
import logging
from datetime import datetime, timedelta, timezone

import typer

from rs_spy.config import get_settings
from rs_spy.data.alpaca_client import AlpacaClient
from rs_spy.data.ingest import backfill
from rs_spy.data.warehouse import connect
from rs_spy.universe import load_universe

app = typer.Typer()


@app.command()
def main(
    years: int = 5,
    universe_file: str = typer.Option("universe.yaml", help="universe YAML in config/"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    universe = load_universe(settings.config_dir / universe_file)
    client = AlpacaClient(settings)
    con = connect(settings.resolved_warehouse_path())

    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=365 * years)

    typer.echo(
        f"Backfilling daily bars for {len(universe.all_symbols)} symbols, "
        f"{start.date()} -> {end.date()}"
    )
    backfill(con, client, universe.all_symbols, "day", start, end)

    row_count = con.execute("SELECT count(*) FROM bars WHERE timespan = 'day'").fetchone()[0]
    typer.echo(f"Done. {row_count} total daily rows in warehouse.")


if __name__ == "__main__":
    app()
