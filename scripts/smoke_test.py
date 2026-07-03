"""M0 smoke test: confirm Alpaca auth works and response shapes match expectations.

Not a capability gate (unlike the old Polygon probe) -- minute bars are already
known to be available on Alpaca's free tier. This just catches config/auth
mistakes early and prints the real data shape for sanity-checking.
"""
from datetime import datetime, timedelta, timezone

import typer

from rs_spy.config import get_settings
from rs_spy.data.alpaca_client import AlpacaClient

app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    client = AlpacaClient(settings)

    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=7)

    typer.echo("1. Fetching SPY daily bars...")
    daily = client.fetch_bars(["SPY"], "day", start, end)
    typer.echo(f"   -> {len(daily)} rows")
    if daily.empty:
        typer.echo("   FAIL: no daily bars returned", err=True)
        raise typer.Exit(1)
    typer.echo(f"   sample row:\n{daily.iloc[-1]}")

    last_ts = daily["ts"].max()
    day_start = last_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    typer.echo(f"\n2. Fetching SPY minute bars for {day_start.date()}...")
    minute = client.fetch_bars(["SPY"], "minute", day_start, day_end)
    typer.echo(f"   -> {len(minute)} rows")
    if minute.empty:
        typer.echo("   FAIL: no minute bars returned", err=True)
        raise typer.Exit(1)

    minute = minute.sort_values("ts")
    spacing = minute["ts"].diff().dt.total_seconds().dropna()
    typer.echo(f"   median spacing: {spacing.median():.0f}s (expect ~60s)")
    typer.echo(f"   sample row:\n{minute.iloc[-1]}")

    if len(minute) < 300:
        typer.echo(
            f"   WARNING: only {len(minute)} minute rows for one session "
            "(expected ~390 for full RTH coverage) -- check market holiday/half-day.",
        )

    typer.echo("\nSmoke test passed.")


if __name__ == "__main__":
    app()
