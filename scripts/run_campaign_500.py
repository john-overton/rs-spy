"""M10 campaign driver: create + launch the cohort runs for selected variants.

    python scripts/run_campaign_500.py --tag jul05                  # all variants
    python scripts/run_campaign_500.py --tag jul05 --variant baseline
    python scripts/run_campaign_500.py --tag jul05 --max-parallel 2

Each run is a detached process (jobs/launch); this driver stays up polling
Postgres and launching the next run as slots free. Ctrl-C is safe for already-
launched jobs: they keep running and stay visible in the runs-store by label --
re-attach to them with `--resume` (same tag/variant), which skips creation
and polls this tag's already-queued runs instead of erroring on the
duplicate guard below.
Re-invoking WITHOUT --resume with the same tag+variant is REFUSED (duplicate
guard) -- use --resume, or finish/clean up the prior attempt's runs, or pick
a new tag; launching a *different* variant later under the same tag is fine.
Memory note: max-parallel defaults to 2 (two ~125-symbol prepares fit in
24 GB; four might not).
"""
import logging
import subprocess

import typer

from rs_spy.backtest.campaign import (
    VARIANTS,
    create_campaign_runs,
    existing_campaign_labels,
    poll_and_launch,
    queued_campaign_runs,
    split_cohorts,
)
from rs_spy.config import get_settings
from rs_spy.store import repository as repo
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema
from rs_spy.universe import load_universe

app = typer.Typer()


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


@app.command()
def main(
    tag: str = typer.Option(...),
    variant: list[str] = typer.Option(None, help="subset of variants (default: all)"),
    universe_file: str = "universe_500.yaml",
    n_cohorts: int = 4,
    max_parallel: int = 2,
    poll_seconds: int = 30,
    resume: bool = typer.Option(
        False, "--resume",
        help="skip creation; poll this tag's already-queued runs (e.g. after Ctrl-C)",
    ),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    variants = {k: VARIANTS[k] for k in (variant or VARIANTS)}

    conn = connect_pg(settings.database_url)
    try:
        init_schema(conn)
        if resume:
            run_ids = queued_campaign_runs(conn, tag, list(variants))
            if not run_ids:
                typer.echo(
                    f"refusing: no queued runs found for tag={tag!r} "
                    f"variants={list(variants)} -- nothing to resume"
                )
                raise typer.Exit(code=1)
            typer.echo(f"resuming {len(run_ids)} queued runs; launching <= {max_parallel} at a time")
            final = poll_and_launch(
                conn, run_ids, max_parallel=max_parallel, poll_seconds=poll_seconds,
            )
            labels = {rid: (repo.get_run(conn, rid) or {}).get("label", str(rid))
                      for rid in run_ids}
        else:
            universe = load_universe(settings.config_dir / universe_file)
            cohorts = split_cohorts(universe.universe, n_cohorts=n_cohorts)
            dupes = existing_campaign_labels(conn, tag, list(variants))
            if dupes:
                typer.echo("refusing: runs already exist for this tag+variant "
                           "(use --resume, finish/clean up the prior attempt, "
                           "or pick a new tag):")
                for lbl in dupes:
                    typer.echo(f"  {lbl}")
                raise typer.Exit(code=1)
            created = create_campaign_runs(
                conn, universe_file=universe_file, cohorts=cohorts,
                variants=variants, tag=tag, git_sha=_git_sha(),
            )
            typer.echo(f"created {len(created)} runs; launching <= {max_parallel} at a time")
            final = poll_and_launch(
                conn, [rid for rid, _ in created],
                max_parallel=max_parallel, poll_seconds=poll_seconds,
            )
            labels = dict(created)
    finally:
        conn.close()
    for rid, status in final.items():
        typer.echo(f"{labels[rid]}: {status}")
    if any(s != "succeeded" for s in final.values()):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
