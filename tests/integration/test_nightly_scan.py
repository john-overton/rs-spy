"""Nightly orchestration against ephemeral Postgres + tmp DuckDB files.

Uses the pg_conn fixture (testcontainers, auto-skip without Docker) and a
FakeClient -- no network, no real warehouse.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from rs_spy.data.alpaca_client import ASSET_COLUMNS, BAR_COLUMNS
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.nightly import run_nightly
from rs_spy.store import scan_repository as scan_repo

pytestmark = pytest.mark.integration

CFG = ScanConfig()
AS_OF = pd.Timestamp("2026-07-02")  # a Thursday
END = datetime(2026, 7, 3, tzinfo=timezone.utc)


def _settings(tmp_path):
    from rs_spy.config import Settings

    # config_dir points at tmp_path (never read: the `curated` fixture stubs load_universe)
    return Settings(
        alpaca_api_key_id="k", alpaca_api_secret_key="s",
        data_dir=tmp_path / "data", config_dir=tmp_path, reports_dir=tmp_path / "reports",
        scan_warehouse_path=tmp_path / "scan.duckdb",
        warehouse_path=tmp_path / "warehouse.duckdb",
    )


class FakeClient:
    def __init__(self, actives=("HOOD", "SPYX")):
        self._actives = list(actives)

    def fetch_assets(self):
        rows = [
            {"symbol": s, "name": f"{s} Common Stock", "exchange": "NYSE", "tradable": True,
             "shortable": True, "fractionable": True, "optionable": True}
            for s in ["HOOD", "SOFI"]
        ]
        rows.append({"symbol": "SPYX", "name": "SPDR Something ETF", "exchange": "ARCA",
                     "tradable": True, "shortable": True, "fractionable": True, "optionable": True})
        return pd.DataFrame(rows, columns=ASSET_COLUMNS)

    def fetch_bars(self, symbols, timespan, start, end):
        days = pd.bdate_range(max(start.date(), (END - timedelta(days=800)).date()),
                              (end - timedelta(days=1)).date(), tz="UTC")
        if timespan == "minute":
            idx = pd.DatetimeIndex(
                [d + pd.Timedelta(hours=14, minutes=30 + i) for d in days for i in range(3)]
            )
        else:
            idx = pd.DatetimeIndex(days)
        rows = [
            {"symbol": s, "timespan": timespan, "ts": t, "open": 50.0, "high": 51.0,
             "low": 49.0, "close": 50.0, "volume": int(CFG.min_adv_shares * 2),
             "vwap": 50.0, "trade_count": 100}
            for s in symbols for t in idx
        ]
        return pd.DataFrame(rows, columns=BAR_COLUMNS)

    def fetch_screener_snapshots(self, top_actives=100, top_movers=50):
        return {
            "most_actives_volume": {"most_actives": [
                {"symbol": s, "volume": 1e8, "trade_count": 1e5} for s in self._actives
            ]},
            "most_actives_trades": {"most_actives": []},
            "market_movers": {"gainers": [], "losers": []},
        }


@pytest.fixture
def launched(monkeypatch):
    """Capture launch_run calls instead of spawning subprocesses."""
    calls = []
    monkeypatch.setattr("rs_spy.scan.nightly.launch_run", lambda run_id, **kw: calls.append(run_id))
    return calls


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin nightly._today_et() to AS_OF so tests that want a "today" (not
    backdated) run don't flake as the real wall-clock date moves on."""
    monkeypatch.setattr("rs_spy.scan.nightly._today_et", lambda: AS_OF)
    return AS_OF


@pytest.fixture
def curated(monkeypatch):
    """Nightly loads universe.yaml only for the curated-symbol set; fake it."""
    from rs_spy.universe import BenchmarkSpec, SymbolSpec, Universe

    fake = Universe(
        benchmarks=[BenchmarkSpec(symbol="SPY", role="primary"),
                    BenchmarkSpec(symbol="QQQ", role="secondary")],
        universe=[SymbolSpec(symbol="AAPL", sector="Technology")],
    )
    monkeypatch.setattr("rs_spy.scan.nightly.load_universe", lambda path: fake)
    return fake


def test_happy_path_scan_screener_onboard_launch(tmp_path, pg_conn, launched, curated, frozen_today):
    report = run_nightly(_settings(tmp_path), FakeClient(), pg_conn,
                         as_of=AS_OF, config=CFG, launch=True)
    assert report.scan_saved and report.screener_saved
    assert report.n_passed == 2  # HOOD, SOFI pass; SPYX fails listing (ARCA + ETF name)
    # snapshot + funnel + parquet artifact landed
    assert scan_repo.get_scan_funnel(pg_conn, AS_OF.date())["passed"] == 2
    assert (tmp_path / "reports" / "universe_scan" / f"{AS_OF.date()}.parquet").exists()
    # HOOD onboarded (top active, passes, not curated); SPYX filtered out
    assert report.onboarded == ["HOOD"]
    onboarded = scan_repo.list_onboarded(pg_conn)
    assert list(onboarded["symbol"]) == ["HOOD"]
    # a tagged run was created with the onboarded symbol and launched
    assert len(launched) == 1
    from rs_spy.store import repository as repo

    run = repo.get_run(pg_conn, launched[0])
    assert run["status"] == "queued"
    assert run["config"]["extra_symbols"] == ["HOOD"]


def test_screener_failure_does_not_block_the_scan(tmp_path, pg_conn, launched, curated, frozen_today):
    client = FakeClient()
    client.fetch_screener_snapshots = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
    report = run_nightly(_settings(tmp_path), client, pg_conn, as_of=AS_OF, config=CFG)
    assert report.scan_saved is True
    assert report.screener_saved is False
    assert any("screener" in e for e in report.errors)
    assert report.onboarded == []  # no payload -> no onboarding, but no crash


def test_second_night_is_idempotent_no_reonboard_no_new_run(
    tmp_path, pg_conn, launched, curated, frozen_today
):
    settings = _settings(tmp_path)
    run_nightly(settings, FakeClient(), pg_conn, as_of=AS_OF, config=CFG)
    report2 = run_nightly(settings, FakeClient(), pg_conn, as_of=AS_OF, config=CFG)
    assert report2.onboarded == []          # HOOD already onboarded -> skipped
    assert len(launched) == 1               # no second run launched
    assert len(scan_repo.list_onboarded(pg_conn)) == 1


def test_backdated_run_skips_screener_and_onboarding(tmp_path, pg_conn, launched, curated, frozen_today):
    """A --as-of <past date> re-run must never touch the live-only screener
    capture or onboarding: the screener has no as-of parameter, so saving its
    live payload under a past scan_date would overwrite that date's genuine
    archived snapshot (last-write-wins) and poison onboarding with a past
    passing-set crossed with today's most-actives."""
    client = FakeClient()
    calls = []
    client.fetch_screener_snapshots = lambda **kw: calls.append("screener") or (_ for _ in ()).throw(
        AssertionError("screener must not be called for a backdated run")
    )
    yesterday = AS_OF - pd.Timedelta(days=1)
    report = run_nightly(_settings(tmp_path), client, pg_conn, as_of=yesterday, config=CFG, launch=True)
    assert calls == []
    assert report.screener_saved is False
    assert report.onboarded == []
    assert any("backdated" in e for e in report.errors)
    assert scan_repo.get_screener_snapshot(pg_conn, yesterday.date(), "most_actives_volume") is None
    assert scan_repo.list_onboarded(pg_conn).empty
    assert launched == []
    # the scan/PIT half is unaffected: still fully re-runnable
    assert report.scan_saved is True
    assert scan_repo.get_scan_funnel(pg_conn, yesterday.date()) is not None


def test_today_run_still_attempts_screener_and_onboarding(tmp_path, pg_conn, launched, curated, frozen_today):
    report = run_nightly(_settings(tmp_path), FakeClient(), pg_conn, as_of=AS_OF, config=CFG, launch=True)
    assert report.screener_saved is True
    assert report.onboarded == ["HOOD"]
    assert not any("backdated" in e for e in report.errors)


def test_parquet_write_failure_does_not_undo_the_committed_scan_save(
    tmp_path, pg_conn, launched, curated, frozen_today, monkeypatch
):
    """Fix 4: the parquet artifact is a convenience export, written after
    scan_repo.save_scan() has already committed to Postgres. A failure
    writing it must land in report.errors but must NOT flip scan_saved back
    to False -- the DB save already succeeded and is the source of truth."""
    monkeypatch.setattr(
        "pandas.DataFrame.to_parquet",
        lambda self, *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
    )
    report = run_nightly(_settings(tmp_path), FakeClient(), pg_conn, as_of=AS_OF, config=CFG)
    assert report.scan_saved is True
    assert any("parquet" in e for e in report.errors)
    assert scan_repo.get_scan_funnel(pg_conn, AS_OF.date()) is not None


def test_tz_aware_as_of_is_normalized_to_naive_utc(tmp_path, pg_conn, launched, curated, monkeypatch):
    """Fix 4: a tz-aware as_of must be normalized (aware -> UTC -> naive)
    exactly like run_universe_scan does, so scan_date/report don't drift by
    timezone. Anchors "today" to a tz-aware moment on the same UTC calendar
    date as AS_OF."""
    aware_today = AS_OF.tz_localize("UTC")
    monkeypatch.setattr("rs_spy.scan.nightly._today_et", lambda: AS_OF)
    report = run_nightly(
        _settings(tmp_path), FakeClient(), pg_conn, as_of=aware_today, config=CFG, launch=False
    )
    assert report.scan_date == AS_OF.date()
    assert report.screener_saved is True  # not treated as backdated


def test_screener_capture_runs_before_refresh_and_scan(tmp_path, pg_conn, launched, curated, frozen_today):
    """Fix 2: the screener snapshot is independent of the scan and must be
    captured before the slow refresh+scan stage, so a slow/failed refresh
    can never cost the day's irreplaceable snapshot."""
    client = FakeClient()
    call_order = []
    orig_screener = client.fetch_screener_snapshots
    orig_bars = client.fetch_bars
    client.fetch_screener_snapshots = lambda **kw: (call_order.append("screener"), orig_screener(**kw))[1]
    client.fetch_bars = lambda *a, **kw: (call_order.append("bars"), orig_bars(*a, **kw))[1]
    run_nightly(_settings(tmp_path), client, pg_conn, as_of=AS_OF, config=CFG)
    assert call_order[0] == "screener"
    assert "bars" in call_order


class FreshIPOClient:
    """HOOD "IPO'd" at `ipo_date` (limited history); SOFI has deep history
    throughout. Used to exercise the onboarding-maintenance maturation path
    across two nightly runs separated by a long stretch of wall-clock time."""

    def __init__(self, actives, ipo_date, end):
        self._actives = list(actives)
        self._ipo_date = ipo_date
        self._end = end

    def fetch_assets(self):
        rows = [
            {"symbol": s, "name": f"{s} Common Stock", "exchange": "NYSE", "tradable": True,
             "shortable": True, "fractionable": True, "optionable": True}
            for s in ["HOOD", "SOFI"]
        ]
        return pd.DataFrame(rows, columns=ASSET_COLUMNS)

    def fetch_bars(self, symbols, timespan, start, end):
        rows = []
        for s in symbols:
            floor = self._ipo_date.date() if s == "HOOD" else (self._end - timedelta(days=3000)).date()
            days = pd.bdate_range(max(start.date(), floor), (end - timedelta(days=1)).date(), tz="UTC")
            if len(days) == 0:
                continue
            if timespan == "minute":
                idx = pd.DatetimeIndex(
                    [d + pd.Timedelta(hours=14, minutes=30 + i) for d in days for i in range(3)]
                )
            else:
                idx = pd.DatetimeIndex(days)
            for t in idx:
                rows.append({"symbol": s, "timespan": timespan, "ts": t, "open": 50.0, "high": 51.0,
                             "low": 49.0, "close": 50.0, "volume": int(CFG.min_adv_shares * 2),
                             "vwap": 50.0, "trade_count": 100})
        return pd.DataFrame(rows, columns=BAR_COLUMNS)

    def fetch_screener_snapshots(self, top_actives=100, top_movers=50):
        return {
            "most_actives_volume": {"most_actives": [
                {"symbol": s, "volume": 1e8, "trade_count": 1e5} for s in self._actives
            ]},
            "most_actives_trades": {"most_actives": []},
            "market_movers": {"gainers": [], "losers": []},
        }


def test_insufficient_history_symbol_matures_via_maintenance(
    tmp_path, pg_conn, launched, curated, monkeypatch
):
    """Fix 3(a): an insufficient_history symbol is otherwise never
    re-evaluated. Night 1 onboards HOOD (a "recent IPO", insufficient).
    Night 2, over a year later, onboards SOFI as a fresh candidate; the
    onboarding-stage maintenance pass should have matured HOOD by then, and
    the launched run's extra_symbols should include both."""
    settings = _settings(tmp_path)
    ipo_date = pd.Timestamp("2026-06-01")  # ~1 month of history as of AS_OF
    end1 = (AS_OF + pd.Timedelta(days=1)).tz_localize(timezone.utc).to_pydatetime()

    monkeypatch.setattr("rs_spy.scan.nightly._today_et", lambda: AS_OF)
    report1 = run_nightly(
        settings, FreshIPOClient(actives=["HOOD"], ipo_date=ipo_date, end=end1), pg_conn,
        as_of=AS_OF, config=CFG, launch=True,
    )
    assert report1.onboarded == ["HOOD"]
    onboarded1 = scan_repo.list_onboarded(pg_conn)
    assert bool(onboarded1.loc[onboarded1.symbol == "HOOD", "insufficient_history"].item()) is True
    assert launched == []  # HOOD is the only onboarded symbol and it's insufficient -> no active set

    later = AS_OF + pd.Timedelta(days=732)  # ~2 years later, a Monday (bdate_range needs a trading day)
    end2 = (later + pd.Timedelta(days=1)).tz_localize(timezone.utc).to_pydatetime()
    monkeypatch.setattr("rs_spy.scan.nightly._today_et", lambda: later)
    report2 = run_nightly(
        settings, FreshIPOClient(actives=["SOFI"], ipo_date=ipo_date, end=end2), pg_conn,
        as_of=later, config=CFG, launch=True,
    )
    onboarded2 = scan_repo.list_onboarded(pg_conn)
    assert bool(onboarded2.loc[onboarded2.symbol == "HOOD", "insufficient_history"].item()) is False
    assert "SOFI" in report2.onboarded
    assert len(launched) == 1
    from rs_spy.store import repository as repo

    run = repo.get_run(pg_conn, launched[0])
    assert set(run["config"]["extra_symbols"]) == {"HOOD", "SOFI"}
