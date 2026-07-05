"""Scan-store round-trips against real Postgres (testcontainers, auto-skip)."""
from datetime import date

import pandas as pd

from rs_spy.store import scan_repository as scan_repo

SCAN_DATE = date(2026, 7, 2)


def _evaluated():
    df = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "name": ["Aaa Corp", "Bbb Inc"],
            "exchange": ["NYSE", "NASDAQ"],
            "optionable": [True, False],
            "last_close": [50.0, float("nan")],
            "adv_shares": [100_000.0, float("nan")],
            "adv_dollars": [5_000_000.0, float("nan")],
            "n_bars": [20, 0],
            "passed": [True, False],
            "first_fail": [None, "coverage"],
        }
    ).set_index("symbol")
    return df


def test_save_scan_roundtrip_and_rerun_is_convergent(pg_conn):
    funnel = {"assets": 2, "fail_listing": 0, "fail_coverage": 1, "fail_price": 0,
              "fail_adv_shares": 0, "fail_adv_dollars": 0, "passed": 1}
    scan_repo.save_scan(pg_conn, SCAN_DATE, _evaluated(), funnel)
    scan_repo.save_scan(pg_conn, SCAN_DATE, _evaluated(), funnel)  # idempotent re-run

    df = scan_repo.get_universe_snapshot(pg_conn, SCAN_DATE)
    assert len(df) == 2  # no duplicates from the re-run
    assert scan_repo.get_scan_funnel(pg_conn, SCAN_DATE) == funnel
    passed = scan_repo.get_universe_snapshot(pg_conn, SCAN_DATE, passed_only=True)
    assert list(passed["symbol"]) == ["AAA"]
    # NaN metrics stored as NULL, first_fail None round-trips
    bbb = df[df.symbol == "BBB"].iloc[0]
    assert bbb["last_close"] is None or pd.isna(bbb["last_close"])
    assert bbb["first_fail"] == "coverage"


def test_screener_snapshot_upsert_roundtrip(pg_conn):
    payload = {"most_actives": [{"symbol": "HOOD", "volume": 1e8}]}
    scan_repo.save_screener_snapshot(pg_conn, SCAN_DATE, "most_actives_volume", payload)
    payload2 = {"most_actives": [{"symbol": "SOFI", "volume": 2e8}]}
    scan_repo.save_screener_snapshot(pg_conn, SCAN_DATE, "most_actives_volume", payload2)
    got = scan_repo.get_screener_snapshot(pg_conn, SCAN_DATE, "most_actives_volume")
    assert got["most_actives"][0]["symbol"] == "SOFI"  # last write wins
    assert scan_repo.get_screener_snapshot(pg_conn, SCAN_DATE, "market_movers") is None


def test_record_onboarded_first_insert_wins(pg_conn):
    first = scan_repo.record_onboarded(
        pg_conn, "HOOD", SCAN_DATE, source="most_actives_volume",
        history_start=date(2021, 7, 30), n_daily_bars=1200, insufficient_history=False,
    )
    again = scan_repo.record_onboarded(
        pg_conn, "HOOD", date(2026, 7, 3), source="most_actives_volume",
        history_start=date(2021, 7, 30), n_daily_bars=1200, insufficient_history=False,
    )
    assert first is True and again is False
    df = scan_repo.list_onboarded(pg_conn)
    assert list(df["symbol"]) == ["HOOD"]
    assert df.iloc[0]["onboarded_date"] == SCAN_DATE  # original row untouched


def test_list_onboarded_empty_has_columns(pg_conn):
    df = scan_repo.list_onboarded(pg_conn)
    assert df.empty
    assert "insufficient_history" in df.columns
