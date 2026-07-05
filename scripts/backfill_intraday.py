"""M4: backfill minute bars for the curated universe (config/universe.yaml).

Minute-bar volume is ~390x daily volume/symbol-day, so this uses month-level
chunks (not year) and a small per-request symbol batch (default: 1 symbol/
call) instead of daily backfill's "whole universe in one call." A single
symbol's month of minute bars is ~8.2k rows (390 bars/day * ~21 trading
days), comfortably under Alpaca's ~10k-row single-page response limit --
keeping each call to one real HTTP request so rate_limiter.SlidingWindowLimiter
(200 calls/min, Alpaca's actual free-tier limit) paces the run accurately.
At the default batch size, 5 years x 130 symbols is ~7,800 calls, ~39
minutes minimum. Safe to interrupt (Ctrl-C) and re-run -- see
rs_spy.data.ingest / manifest for the resumability mechanism.
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
    symbol_batch_size: int = 1,
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
        f"Backfilling minute bars for {len(universe.all_symbols)} symbols, "
        f"{start.date()} -> {end.date()} (month chunks, batch size {symbol_batch_size})"
    )
    backfill(
        con,
        client,
        universe.all_symbols,
        "minute",
        start,
        end,
        chunk_freq="month",
        symbol_batch_size=symbol_batch_size,
    )

    row_count = con.execute("SELECT count(*) FROM bars WHERE timespan = 'minute'").fetchone()[0]
    typer.echo(f"Done. {row_count} total minute rows in warehouse.")


if __name__ == "__main__":
    app()
