"""Universe builder plumbing: first-bar query + staleness refusal (hermetic)."""
from datetime import date, timedelta
from pathlib import Path

import importlib.util
import sys

import pandas as pd
import pytest

from rs_spy.data.warehouse import connect
from rs_spy.scan.universe500 import unknown_fraction

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_universe_500.py"
spec = importlib.util.spec_from_file_location("build_universe_500", SCRIPT)
builder = importlib.util.module_from_spec(spec)
sys.modules["build_universe_500"] = builder
spec.loader.exec_module(builder)


def _seed(con, symbol, first_day):
    days = pd.bdate_range(first_day, periods=3)
    for ts in days:
        con.execute(
            "INSERT INTO bars VALUES (?, 'day', ?, 1, 1, 1, 1, 100, 1, 1)",
            [symbol, ts.to_pydatetime()],
        )


def test_first_daily_bars_returns_min_ts_per_requested_symbol():
    con = connect(Path(":memory:"))
    _seed(con, "OLD", "2020-01-02")
    _seed(con, "NEW", "2024-03-01")
    _seed(con, "IGNORED", "2019-01-02")
    out = builder.first_daily_bars(con, ["OLD", "NEW", "GHOST"])
    assert out["OLD"] == pd.Timestamp("2020-01-02")
    assert out["NEW"] == pd.Timestamp("2024-03-01")
    assert "GHOST" not in out and "IGNORED" not in out


class _FakeCursor:
    def __init__(self, newest):
        self._newest = newest
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql):
        return self
    def fetchone(self):
        return {"scan_date": self._newest} if self._newest else None


class _FakeConn:
    def __init__(self, newest):
        self._newest = newest
    def cursor(self):
        return _FakeCursor(self._newest)


def test_latest_scan_date_accepts_fresh_and_refuses_stale():
    fresh = date.today() - timedelta(days=2)
    assert builder.latest_scan_date(_FakeConn(fresh)) == fresh
    with pytest.raises(builder.StaleScanError):
        builder.latest_scan_date(_FakeConn(date.today() - timedelta(days=10)))
    with pytest.raises(builder.StaleScanError):
        builder.latest_scan_date(_FakeConn(None))


def _doc(curated_count, topup_sectors):
    """A build_universe_yaml-shaped doc: `curated_count` curated entries
    (never UNKNOWN) followed by one top-up entry per sector in `topup_sectors`."""
    universe = (
        [{"symbol": f"CUR{i}", "sector": "Technology"} for i in range(curated_count)]
        + [{"symbol": f"TOP{i}", "sector": s} for i, s in enumerate(topup_sectors)]
    )
    return {"benchmarks": [], "universe": universe}


def test_unknown_fraction_counts_only_topup_entries():
    doc = _doc(curated_count=2, topup_sectors=["UNKNOWN", "Technology", "UNKNOWN", "Financials"])
    # 2 of 4 top-up entries UNKNOWN; curated prefix excluded from the count.
    assert unknown_fraction(doc, curated_count=2) == 0.5


def test_unknown_fraction_all_known_is_zero():
    doc = _doc(curated_count=1, topup_sectors=["Technology", "Financials"])
    assert unknown_fraction(doc, curated_count=1) == 0.0


def test_unknown_fraction_no_topup_entries_is_zero():
    doc = _doc(curated_count=3, topup_sectors=[])
    assert unknown_fraction(doc, curated_count=3) == 0.0
