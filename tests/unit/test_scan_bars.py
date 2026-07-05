"""Scan-warehouse refresh: separate DuckDB file, manifest backfill + self-healing tail."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from rs_spy.config import Settings
from rs_spy.data.alpaca_client import BAR_COLUMNS
from rs_spy.scan.bars import connect_scan, refresh_daily_bars

END = datetime(2026, 7, 2, tzinfo=timezone.utc)


class FakeClient:
    """Serves synthetic daily bars for any requested [start, end) window."""

    def __init__(self):
        self.calls: list[tuple[list[str], datetime, datetime]] = []

    def fetch_bars(self, symbols, timespan, start, end):
        assert timespan == "day"
        self.calls.append((list(symbols), start, end))
        days = pd.bdate_range(start.date(), (end - timedelta(days=1)).date(), tz="UTC")
        rows = [
            {"symbol": s, "timespan": "day", "ts": d, "open": 10.0, "high": 11.0,
             "low": 9.0, "close": 10.5, "volume": 50_000, "vwap": 10.4, "trade_count": 100}
            for s in symbols for d in days
        ]
        return pd.DataFrame(rows, columns=BAR_COLUMNS)


def test_settings_scan_warehouse_path_defaults_beside_the_main_warehouse():
    s = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    assert s.resolved_scan_warehouse_path() == s.data_dir / "scan.duckdb"
    s2 = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s",
                  scan_warehouse_path=Path("/tmp/x.duckdb"))
    assert s2.resolved_scan_warehouse_path() == Path("/tmp/x.duckdb")


def test_refresh_writes_history_and_rerun_only_fetches_the_tail():
    con = connect_scan(Path(":memory:"))
    client = FakeClient()
    refresh_daily_bars(con, client, ["AAA", "BBB"], END, years=1, tail_days=7)
    n1 = con.execute("SELECT count(*) FROM bars WHERE timespan='day'").fetchone()[0]
    assert n1 > 0
    first_pass_calls = len(client.calls)

    refresh_daily_bars(con, client, ["AAA", "BBB"], END, years=1, tail_days=7)
    # second pass: the manifest skips every historical year unit -> only the
    # unconditional tail fetch remains (one call for this single batch)
    assert len(client.calls) == first_pass_calls + 1
    tail_symbols, tail_start, _ = client.calls[-1]
    assert tail_symbols == ["AAA", "BBB"]
    assert tail_start >= END - timedelta(days=8)
    n2 = con.execute("SELECT count(*) FROM bars WHERE timespan='day'").fetchone()[0]
    assert n2 == n1  # upsert idempotent, no duplicate rows


def test_tail_start_self_heals_back_to_the_newest_stored_bar():
    con = connect_scan(Path(":memory:"))
    client = FakeClient()
    stale_end = END - timedelta(days=30)
    refresh_daily_bars(con, client, ["AAA"], stale_end, years=1, tail_days=7)

    refresh_daily_bars(con, client, ["AAA"], END, years=1, tail_days=7)
    _, tail_start, _ = client.calls[-1]
    # 30 days of gap > tail_days -> the tail must reach back to the newest
    # stored bar, not just END - 7d
    assert tail_start <= stale_end


def test_symbol_batching_splits_large_symbol_lists():
    con = connect_scan(Path(":memory:"))
    client = FakeClient()
    symbols = [f"S{i:03d}" for i in range(5)]
    refresh_daily_bars(con, client, symbols, END, years=1, tail_days=7, symbol_batch_size=2)
    # every fetch call carries at most 2 symbols
    assert all(len(c[0]) <= 2 for c in client.calls)
