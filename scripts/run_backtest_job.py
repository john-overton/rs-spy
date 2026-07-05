"""DB-native single M5 backtest run: execute and record to the Postgres
runs-store. Invoked by the job launcher (detached subprocess) or standalone.

    # run an existing queued run (created by the UI):
    python scripts/run_backtest_job.py --run-id <uuid>

    # create + run in one shot (standalone CLI), with config overrides:
    python scripts/run_backtest_job.py --config-json '{"shorts_enabled": true}' --label demo

Needs Postgres up (docker compose up -d) and a populated warehouse.
"""
import json
import uuid
from dataclasses import replace

import typer

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.jobs.runner import run_job

app = typer.Typer()


@app.command()
def main(
    run_id: str = typer.Option(None, help="UUID of an existing queued run to execute"),
    config_json: str = typer.Option(None, help="JSON of BacktestConfigM5 overrides (creates a new run)"),
    label: str = typer.Option(None, help="Optional label when creating a run"),
) -> None:
    if run_id is None and config_json is None:
        raise typer.BadParameter("provide --run-id or --config-json")

    parsed_run_id = uuid.UUID(run_id) if run_id else None
    config = None
    if config_json is not None:
        overrides = json.loads(config_json)
        if "disabled_gates" in overrides:
            overrides["disabled_gates"] = frozenset(overrides["disabled_gates"])
        config = replace(BacktestConfigM5(), **overrides)

    result_id = run_job(parsed_run_id, config, label=label)
    typer.echo(f"run {result_id} succeeded")


if __name__ == "__main__":
    app()
