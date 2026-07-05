"""M10 cohort campaign: split the 500 into cohorts, launch <=k detached jobs.

Cohorts exist because one process cannot hold 500 symbols of minute bars on a
24 GB machine (the M7.5 sweep already OOM'd at 130). Documented caveat:
portfolio-level constraints (max-concurrent, loss limits, lockouts) apply per
cohort, not across the whole 500 -- right for signal-quality/sample-size
questions, not a literal portfolio simulation (see the M10 spec).
"""
import dataclasses
import time
import uuid

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.jobs.launch import launch_run
from rs_spy.store import repository as repo
from rs_spy.universe import SymbolSpec

TERMINAL = {"succeeded", "failed"}

# Campaign variants: config overrides per dataclasses.replace. rrs_m5_window is
# prepare-baked (engine_m5 docstring) -- each such run pays its own precompute;
# fine, every cohort run is its own process anyway.
VARIANTS: dict[str, dict] = {
    "baseline": {},
    "w12": {"rrs_m5_window": 12},
    "w24": {"rrs_m5_window": 24},
    "hold2": {"bias_hold_bars": 2},
    "shorts": {"shorts_enabled": True},
}


def split_cohorts(symbol_specs: list[SymbolSpec], n_cohorts: int = 4) -> list[list[str]]:
    """Deterministic sector-stratified round-robin. Sorting by (sector, symbol)
    before dealing makes the split independent of input order and spreads each
    sector across all cohorts (so the per-sector cap binds evenly). Caveat: the
    round-robin phase carries across sector boundaries, so uneven sector sizes
    can drift cohort balance slightly (deliberate design limitation)."""
    ordered = sorted(symbol_specs, key=lambda s: (s.sector, s.symbol))
    cohorts: list[list[str]] = [[] for _ in range(n_cohorts)]
    for i, spec in enumerate(ordered):
        cohorts[i % n_cohorts].append(spec.symbol)
    return cohorts


def existing_campaign_labels(conn, tag: str, variants: list[str]) -> list[str]:
    """Labels of runs already created for this (tag, variant) combination.

    Duplicate-launch guard: re-invoking the driver with the same tag+variant
    would silently create and launch a second full set of runs, so the driver
    refuses when this returns anything. Scoped per (tag, variant), NOT per tag,
    because the intended flow launches --variant baseline first and the
    remaining variants later under the SAME tag."""
    found: list[str] = []
    with conn.cursor() as cur:
        for vname in variants:
            cur.execute(
                "SELECT label FROM runs WHERE label LIKE %s ORDER BY label",
                (f"m10-{tag}-{vname}-c%",),
            )
            found.extend(row["label"] for row in cur.fetchall())
    return found


def create_campaign_runs(
    conn,
    *,
    universe_file: str,
    cohorts: list[list[str]],
    variants: dict[str, dict],
    tag: str,
    git_sha: str | None = None,
) -> list[tuple[uuid.UUID, str]]:
    """One queued Postgres run per variant x cohort. Returns (run_id, label)."""
    out = []
    for vname, overrides in variants.items():
        for n, cohort in enumerate(cohorts, start=1):
            config = dataclasses.replace(
                BacktestConfigM5(**overrides),
                universe_file=universe_file,
                trade_symbols_override=tuple(cohort),
            )
            label = f"m10-{tag}-{vname}-c{n}"
            run_id = repo.create_run(conn, config, label=label, git_sha=git_sha)
            out.append((run_id, label))
    return out


def poll_and_launch(
    conn,
    run_ids: list[uuid.UUID],
    *,
    max_parallel: int = 2,
    poll_seconds: int = 30,
    launch=launch_run,
    sleep=time.sleep,
    get_run=repo.get_run,
) -> dict[uuid.UUID, str]:
    """Launch queued runs FIFO, keeping <= max_parallel non-terminal at once;
    poll until all are terminal. launch/sleep/get_run injectable for tests."""
    pending = list(run_ids)
    live: list[uuid.UUID] = []
    final: dict[uuid.UUID, str] = {}
    while pending or live:
        still_live = []
        for rid in live:
            status = (get_run(conn, rid) or {}).get("status", "failed")
            if status in TERMINAL:
                final[rid] = status
            else:
                still_live.append(rid)
        live = still_live
        while pending and len(live) < max_parallel:
            rid = pending.pop(0)
            launch(rid)
            live.append(rid)
        if pending or live:
            sleep(poll_seconds)
    return final
