"""Onboarding: gate-filtered candidate selection + per-symbol dual backfill."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from rs_spy.data import manifest
from rs_spy.data.alpaca_client import BAR_COLUMNS
from rs_spy.scan.bars import connect_scan
from rs_spy.scan.onboarding import (
    MIN_HISTORY_DAYS,
    OnboardingOutcome,
    onboard_symbol,
    select_onboarding_candidates,
)

END = datetime(2026, 7, 2, tzinfo=timezone.utc)


def _payload(symbols):
    return {"most_actives": [{"symbol": s, "volume": 1e8, "trade_count": 1e5} for s in symbols]}


def test_candidates_are_gate_filtered_deduped_and_skip_known_symbols():
    payload = _payload(["SPY", "HOOD", "PENNY", "AAPL", "HOOD", "SOFI", "NEW1"])
    out = select_onboarding_candidates(
        payload,
        passing={"HOOD", "SOFI", "NEW1", "AAPL"},   # SPY (ETF) and PENNY failed the scan
        curated={"AAPL"},                            # already in universe.yaml
        onboarded={"NEW1"},                          # onboarded a previous night
    )
    assert out == ["HOOD", "SOFI"]


def test_candidates_respect_top_n():
    payload = _payload([f"S{i}" for i in range(15)])
    out = select_onboarding_candidates(
        payload, passing={f"S{i}" for i in range(15)}, curated=set(), onboarded=set(), top_n=10
    )
    assert out == [f"S{i}" for i in range(10)]


def test_cap_applies_to_raw_ranking_before_gate_filtering():
    # Spec: "Most-active auto-onboarding" (2026-07-05-universe-scan-design.md) --
    # the candidate pool is the raw top-N; gates filter WITHIN it, so a top-10
    # of all-failing symbols yields zero candidates even if passers rank 11-15.
    payload = _payload([f"ETF{i}" for i in range(10)] + [f"OK{i}" for i in range(5)])
    out = select_onboarding_candidates(
        payload,
        passing={f"OK{i}" for i in range(5)},
        curated=set(),
        onboarded=set(),
        top_n=10,
    )
    assert out == []


def test_empty_or_missing_payload_yields_no_candidates():
    assert select_onboarding_candidates({}, passing={"A"}, curated=set(), onboarded=set()) == []


class FakeClient:
    """Daily + minute bars; history_days controls how far back data exists."""

    def __init__(self, history_days=400):
        self.first_day = (END - timedelta(days=history_days)).date()

    def fetch_bars(self, symbols, timespan, start, end):
        days = pd.bdate_range(max(start.date(), self.first_day),
                              (end - timedelta(days=1)).date(), tz="UTC")
        if timespan == "minute":  # 3 RTH-ish minute bars per day is plenty for the test
            idx = pd.DatetimeIndex(
                [d + pd.Timedelta(hours=14, minutes=30 + i) for d in days for i in range(3)]
            )
        else:
            idx = pd.DatetimeIndex(days)
        rows = [
            {"symbol": s, "timespan": timespan, "ts": t, "open": 20.0, "high": 21.0,
             "low": 19.0, "close": 20.5, "volume": 60_000, "vwap": 20.4, "trade_count": 50}
            for s in symbols for t in idx
        ]
        return pd.DataFrame(rows, columns=BAR_COLUMNS)


def test_onboard_symbol_backfills_both_cadences_and_reports_history():
    con = connect_scan(Path(":memory:"))  # same schema as the main warehouse
    out = onboard_symbol(con, FakeClient(history_days=900), "HOOD", END, years=5)
    assert isinstance(out, OnboardingOutcome)
    assert out.n_daily_bars > 0 and out.n_minute_bars > 0
    assert out.insufficient_history is False  # ~900 calendar days > 300 trading bars
    assert out.history_start is not None
    n_min = con.execute(
        "SELECT count(*) FROM bars WHERE symbol='HOOD' AND timespan='minute'"
    ).fetchone()[0]
    assert n_min == out.n_minute_bars


def test_short_history_ipo_is_flagged_insufficient():
    con = connect_scan(Path(":memory:"))
    out = onboard_symbol(con, FakeClient(history_days=90), "FRESH", END, years=5)
    assert 0 < out.n_daily_bars < MIN_HISTORY_DAYS
    assert out.insufficient_history is True


def test_failed_fetches_produce_zero_bar_outcome_not_a_crash():
    class BrokenClient:
        def fetch_bars(self, symbols, timespan, start, end):
            raise ConnectionError("api down")

    con = connect_scan(Path(":memory:"))
    # ingest.backfill records 'error' units and continues; the outcome's zero
    # counts tell the caller NOT to record this symbol as onboarded
    out = onboard_symbol(con, BrokenClient(), "DOWN", END, years=1)
    assert out.n_daily_bars == 0 and out.n_minute_bars == 0


class FlakyOneMonthClient(FakeClient):
    """Like FakeClient, but minute backfill fails for exactly one calendar
    month (a partial-hole outage) unless/until `healed`."""

    def __init__(self, history_days=400, fail_month="2026-05"):
        super().__init__(history_days=history_days)
        self.fail_month = fail_month
        self.healed = False

    def fetch_bars(self, symbols, timespan, start, end):
        unit_key = f"{start.year}-{start.month:02d}"
        if timespan == "minute" and unit_key == self.fail_month and not self.healed:
            raise ConnectionError("simulated month outage")
        return super().fetch_bars(symbols, timespan, start, end)


def test_symbols_with_error_units_detects_and_resume_run_repairs_partial_hole():
    """T6 resume-path test: a partially-failed minute backfill (n_minute_bars
    > 0 overall, but one month unit landed 'error') leaves a permanent hole
    unless something retries it. manifest.symbols_with_error_units detects
    the hole, and re-running onboard_symbol -- already resumable, per its
    docstring -- repairs it once the client heals: the error unit flips to
    'ok' and the hole disappears."""
    con = connect_scan(Path(":memory:"))
    client = FlakyOneMonthClient(history_days=400, fail_month="2026-05")

    out1 = onboard_symbol(con, client, "HOOD", END, years=1)
    assert out1.n_minute_bars > 0  # the other months landed fine
    assert manifest.symbols_with_error_units(con, ["HOOD"]) == ["HOOD"]
    assert con.execute(
        "SELECT status FROM fetch_manifest "
        "WHERE symbol='HOOD' AND timespan='minute' AND unit_key='2026-05'"
    ).fetchone() == ("error",)

    client.healed = True  # the client "now succeeds" for that month
    out2 = onboard_symbol(con, client, "HOOD", END, years=1)
    assert out2.n_minute_bars > out1.n_minute_bars  # the hole's rows landed
    assert manifest.symbols_with_error_units(con, ["HOOD"]) == []
    assert con.execute(
        "SELECT status FROM fetch_manifest "
        "WHERE symbol='HOOD' AND timespan='minute' AND unit_key='2026-05'"
    ).fetchone() == ("ok",)
