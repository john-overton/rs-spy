"""Nightly discovery orchestration: screener -> refresh -> scan -> record -> onboard -> re-run.

Each stage is isolated: a screener failure never blocks the scan snapshot, one
symbol's failed onboarding never blocks the others, and every failure lands in
NightlyReport.errors instead of killing the job. The scan itself refusing
(ScanCoverageError -- holiday/outage) DOES propagate: no snapshot should exist
for such a night.

The screener-capture stage runs FIRST, before the (15-45min) refresh+scan, so
a slow or failed refresh can never cost the day's irreplaceable snapshot.

Backdated re-runs (`as_of` != today ET): `fetch_screener_snapshots` has no
as-of parameter -- it is always "right now". Saving its payload under a past
scan_date would silently overwrite that date's genuine archived snapshot
(save_screener_snapshot is last-write-wins) and poison onboarding with a past
passing-set crossed with today's most-actives. So a backdated run SKIPS the
screener-capture and onboarding stages entirely and only re-runs the scan/PIT
half, which is fully re-runnable by design.

Scheduling (documented, not auto-installed). 17:00 ET capture, RTH-only policy
(see the spec). This machine runs America/Chicago, so 16:00 CT == 17:00 ET:

    crontab -e
    0 16 * * 1-5  cd /Users/johnoverton/Development/rs-spy && .venv/bin/python scripts/run_nightly_scan.py >> logs/nightly_scan.log 2>&1
"""
import logging
from dataclasses import dataclass, field
from datetime import timezone

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.data.warehouse import connect
from rs_spy.jobs.launch import launch_run
from rs_spy.jobs.runner import _git_sha
from rs_spy.scan.bars import connect_scan, refresh_daily_bars
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import run_universe_scan
from rs_spy.scan.onboarding import onboard_symbol, select_onboarding_candidates
from rs_spy.store import repository as repo
from rs_spy.store import scan_repository as scan_repo
from rs_spy.universe import load_universe

logger = logging.getLogger(__name__)


def _today_et() -> pd.Timestamp:
    """"Today" as a naive calendar timestamp in America/New_York -- the
    reference used to decide whether a run is backdated. A thin wrapper so
    tests can pin it instead of depending on the wall clock."""
    return pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)


@dataclass
class NightlyReport:
    scan_date: object
    n_assets: int = 0
    n_passed: int = 0
    scan_saved: bool = False
    screener_saved: bool = False
    onboarded: list = field(default_factory=list)
    launched_run_id: str | None = None
    errors: list = field(default_factory=list)


def run_nightly(
    settings,
    client,
    pg_conn,
    *,
    as_of=None,
    config: ScanConfig | None = None,
    top_n: int = 10,
    onboard: bool = True,
    launch: bool = True,
) -> NightlyReport:
    config = config or ScanConfig()
    if as_of is None:
        as_of = _today_et()
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is not None:  # mirror run_universe_scan's normalization
        as_of = as_of.tz_convert("UTC").tz_localize(None)
    scan_date = as_of.date()
    # backfill/refresh end: exclusive upper bound just past the as-of session
    end = (as_of + pd.Timedelta(days=1)).tz_localize(timezone.utc).to_pydatetime()
    report = NightlyReport(scan_date=scan_date)
    is_backdated = as_of.normalize() != _today_et()
    if is_backdated:
        report.errors.append(
            "backdated run: screener+onboarding skipped (screener endpoints are real-time-only)"
        )

    # 1) screener capture (isolated, real-time-only). Runs BEFORE the
    #    refresh+scan below (independent of scan results) so a slow/failed
    #    15-45min refresh can never cost the day's irreplaceable snapshot.
    #    Skipped entirely for a backdated run -- see module docstring.
    snapshots = None
    if not is_backdated:
        try:
            snapshots = client.fetch_screener_snapshots()
            for endpoint, payload in snapshots.items():
                scan_repo.save_screener_snapshot(pg_conn, scan_date, endpoint, payload)
            report.screener_saved = True
        except Exception as exc:  # noqa: BLE001 -- isolated stage, recorded not raised
            logger.exception("screener capture failed")
            report.errors.append(f"screener: {exc}")

    # 2) assets + broad daily refresh + scan (a ScanCoverageError propagates:
    #    no snapshot must exist for a holiday/outage night). Fully re-runnable,
    #    including for a backdated as_of.
    assets = client.fetch_assets()
    report.n_assets = len(assets)
    scan_con = connect_scan(settings.resolved_scan_warehouse_path())
    try:
        refresh_daily_bars(scan_con, client, assets["symbol"].tolist(), end)
        result = run_universe_scan(scan_con, assets, as_of, config)
    finally:
        scan_con.close()
    report.n_passed = len(result.passing)

    scan_repo.save_scan(pg_conn, scan_date, result.evaluated, result.funnel)
    artifact_dir = settings.reports_dir / "universe_scan"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result.evaluated.to_parquet(artifact_dir / f"{scan_date}.parquet")
    report.scan_saved = True

    # 3) onboarding (isolated per symbol) + tagged re-run. Skipped entirely
    #    for a backdated run -- see module docstring.
    if onboard and not is_backdated and snapshots and snapshots.get("most_actives_volume"):
        _run_onboarding(
            settings, client, pg_conn, snapshots["most_actives_volume"],
            result, end, scan_date, report, top_n=top_n, launch=launch,
        )
    return report


def _run_onboarding(
    settings, client, pg_conn, actives_payload, result, end, scan_date, report,
    *, top_n: int, launch: bool,
) -> None:
    universe = load_universe(settings.config_dir / "universe.yaml")
    already = scan_repo.list_onboarded(pg_conn)
    candidates = select_onboarding_candidates(
        actives_payload,
        passing=set(result.passing),
        curated=set(universe.all_symbols),
        onboarded=set(already["symbol"]),
        top_n=top_n,
    )
    if not candidates:
        return

    newly: list[str] = []
    try:
        wh_con = connect(settings.resolved_warehouse_path())  # MAIN warehouse, read-write
    except Exception as exc:  # noqa: BLE001 -- e.g. another writer holds it; retry next night
        report.errors.append(f"onboarding: warehouse unavailable: {exc}")
        return
    try:
        for sym in candidates:
            try:
                outcome = onboard_symbol(wh_con, client, sym, end)
            except Exception as exc:  # noqa: BLE001 -- per-symbol isolation
                logger.exception("onboarding %s failed", sym)
                report.errors.append(f"onboard {sym}: {exc}")
                continue
            if outcome.n_daily_bars == 0 or outcome.n_minute_bars == 0:
                report.errors.append(f"onboard {sym}: backfill incomplete, will retry")
                continue
            scan_repo.record_onboarded(
                pg_conn, sym, scan_date, source="most_actives_volume",
                history_start=outcome.history_start,
                n_daily_bars=outcome.n_daily_bars,
                insufficient_history=outcome.insufficient_history,
            )
            newly.append(sym)
    finally:
        wh_con.close()
    report.onboarded = newly
    if not (launch and newly):
        return

    # cumulative sufficient-history set -> one tagged run over curated + onboarded
    onboarded = scan_repo.list_onboarded(pg_conn)
    active = sorted(onboarded.loc[~onboarded["insufficient_history"], "symbol"])
    if not active:
        return
    cfg = BacktestConfigM5(extra_symbols=tuple(active))
    run_id = repo.create_run(
        pg_conn, cfg, label=f"onboarding-{scan_date}", git_sha=_git_sha()
    )
    launch_run(run_id)
    report.launched_run_id = str(run_id)
