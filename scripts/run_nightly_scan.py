"""M9 nightly discovery job: refresh broad daily bars, run the universe scan,
record screener snapshots, onboard qualifying most-actives, launch a tagged
backtest over curated + onboarded symbols.

    python scripts/run_nightly_scan.py                    # tonight, iex thresholds
    python scripts/run_nightly_scan.py --as-of 2026-07-01 # re-run a past night's SCAN ONLY --
    #   screener capture + onboarding are live-only and auto-skip whenever
    #   --as-of != today ET (see rs_spy/scan/nightly.py's module docstring)
    python scripts/run_nightly_scan.py --no-onboard       # scan + record only

Needs .env (Alpaca keys) and Postgres up (docker compose up -d). Scheduling:
see rs_spy/scan/nightly.py's docstring (cron at 16:00 America/Chicago ==
17:00 ET, weekdays); the installed crontab entry runs the absolute-path
wrapper scripts/nightly_scan_cron.sh, logging to logs/nightly_scan.log.
Note: macOS cron skips runs while the machine sleeps (launchd would catch
up on wake) -- a missed night self-heals via the manifest + tail refresh,
but that day's screener snapshot is lost (live-only endpoints).
"""
import logging

import typer

from rs_spy.config import get_settings
from rs_spy.data.alpaca_client import AlpacaClient
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import ScanCoverageError
from rs_spy.scan.nightly import run_nightly
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema

app = typer.Typer()


@app.command()
def main(
    as_of: str = typer.Option(None, help="Scan date YYYY-MM-DD (default: today ET)"),
    feed: str = typer.Option("iex", help="Threshold preset: iex or sip"),
    top: int = typer.Option(10, help="Most-active candidates to consider for onboarding"),
    no_onboard: bool = typer.Option(False, help="Skip onboarding entirely"),
    no_launch: bool = typer.Option(False, help="Onboard but don't launch the backtest re-run"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    client = AlpacaClient(settings)
    conn = connect_pg()
    try:
        init_schema(conn)
        report = run_nightly(
            settings, client, conn,
            as_of=as_of, config=ScanConfig.for_feed(feed), top_n=top,
            onboard=not no_onboard, launch=not no_launch,
        )
    except ScanCoverageError as exc:
        typer.echo(f"scan refused: {exc}")
        raise typer.Exit(1) from exc
    finally:
        conn.close()

    typer.echo(
        f"{report.scan_date}: {report.n_passed}/{report.n_assets} passed; "
        f"screener={'ok' if report.screener_saved else 'FAILED'}; "
        f"onboarded={report.onboarded or '[]'}; "
        f"run={report.launched_run_id or '-'}"
    )
    for err in report.errors:
        typer.echo(f"  warning: {err}")


if __name__ == "__main__":
    app()
