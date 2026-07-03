from datetime import datetime, timezone

import pandas as pd
import pytest

from rs_spy.data.alpaca_client import BAR_COLUMNS
from rs_spy.data.ingest import backfill
from rs_spy.data.warehouse import connect


class FakeClient:
    """Duck-types AlpacaClient.fetch_bars without any network access.

    `fail_on` maps a year (int) to an exception instance to raise instead of
    returning data, letting tests simulate transient errors or a hard
    process-kill (via BaseException) partway through a multi-year backfill.
    """

    def __init__(self, fail_on: dict[int, BaseException] | None = None):
        self.fail_on = fail_on or {}
        self.calls: list[tuple[tuple[str, ...], str, int]] = []

    def fetch_bars(self, symbols, timespan, start, end) -> pd.DataFrame:
        self.calls.append((tuple(symbols), timespan, start.year))
        if start.year in self.fail_on:
            raise self.fail_on[start.year]
        rows = [
            {
                "symbol": sym,
                "timespan": timespan,
                "ts": datetime(start.year, 1, 2, tzinfo=timezone.utc),
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 1000,
                "vwap": 1.4,
                "trade_count": 10,
            }
            for sym in symbols
        ]
        return pd.DataFrame(rows, columns=BAR_COLUMNS)


def _bar_years(con) -> set[int]:
    rows = con.execute("SELECT DISTINCT extract(year from ts) FROM bars").fetchall()
    return {int(r[0]) for r in rows}


def test_full_backfill_writes_bars_and_manifest(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    client = FakeClient()
    symbols = ["AAA", "BBB", "CCC"]
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 6, 1, tzinfo=timezone.utc)

    backfill(con, client, symbols, "day", start, end)

    assert len(client.calls) == 1  # single year chunk, all symbols batched
    row_count = con.execute("SELECT count(*) FROM bars").fetchone()[0]
    assert row_count == len(symbols)
    ok_count = con.execute(
        "SELECT count(*) FROM fetch_manifest WHERE status = 'ok'"
    ).fetchone()[0]
    assert ok_count == len(symbols)


def test_second_run_skips_already_fetched_units(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    client = FakeClient()
    symbols = ["AAA", "BBB"]
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 6, 1, tzinfo=timezone.utc)

    backfill(con, client, symbols, "day", start, end)
    assert len(client.calls) == 1

    backfill(con, client, symbols, "day", start, end)
    assert len(client.calls) == 1, "second run should not re-fetch already-ok units"


def test_resume_after_simulated_process_kill(tmp_path):
    """Kill the process (BaseException, not caught by ingest's per-chunk
    Exception handler) partway through a multi-year backfill, then rerun with
    a healthy client and confirm: no duplicate calls for the completed year,
    and the interrupted/remaining years get fetched exactly once."""
    con = connect(tmp_path / "warehouse.duckdb")
    symbols = ["AAA", "BBB"]
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)  # 2021, 2022, 2023 chunks

    killer = FakeClient(fail_on={2022: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        backfill(con, killer, symbols, "day", start, end)

    assert killer.calls == [(("AAA", "BBB"), "day", 2021), (("AAA", "BBB"), "day", 2022)]
    assert _bar_years(con) == {2021}

    healthy = FakeClient()
    backfill(con, healthy, symbols, "day", start, end)

    # 2021 already done -> not re-fetched; 2022 (interrupted) and 2023 (never
    # attempted) get fetched exactly once each.
    assert healthy.calls == [(("AAA", "BBB"), "day", 2022), (("AAA", "BBB"), "day", 2023)]
    assert _bar_years(con) == {2021, 2022, 2023}


def test_symbol_batch_size_issues_one_call_per_batch(tmp_path):
    """Minute backfill uses small symbol batches (default 1) so each call
    stays within Alpaca's single-page response limit -- confirm batching
    splits one chunk into multiple calls and all symbols still land."""
    con = connect(tmp_path / "warehouse.duckdb")
    client = FakeClient()
    symbols = ["AAA", "BBB", "CCC"]
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 15, tzinfo=timezone.utc)

    backfill(con, client, symbols, "minute", start, end, chunk_freq="month", symbol_batch_size=1)

    assert client.calls == [
        (("AAA",), "minute", 2023),
        (("BBB",), "minute", 2023),
        (("CCC",), "minute", 2023),
    ]
    row_count = con.execute("SELECT count(*) FROM bars WHERE timespan = 'minute'").fetchone()[0]
    assert row_count == len(symbols)


def test_symbol_batch_failure_does_not_block_other_batches(tmp_path):
    """A transient failure in one symbol's batch shouldn't stop later
    batches in the same chunk from being fetched (unlike a whole-chunk
    failure when all symbols are batched together)."""
    con = connect(tmp_path / "warehouse.duckdb")
    symbols = ["AAA", "BBB"]
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 15, tzinfo=timezone.utc)

    class BatchFailingClient(FakeClient):
        def fetch_bars(self, symbols, timespan, start, end):
            if symbols == ["AAA"]:
                raise RuntimeError("transient")
            return super().fetch_bars(symbols, timespan, start, end)

    client = BatchFailingClient()
    backfill(con, client, symbols, "minute", start, end, chunk_freq="month", symbol_batch_size=1)

    assert _bar_years(con) == {2023}
    row_count = con.execute("SELECT count(*) FROM bars WHERE timespan = 'minute'").fetchone()[0]
    assert row_count == 1  # only BBB's batch succeeded

    error_count = con.execute(
        "SELECT count(*) FROM fetch_manifest WHERE status = 'error'"
    ).fetchone()[0]
    assert error_count == 1

    healthy = FakeClient()
    backfill(con, healthy, symbols, "minute", start, end, chunk_freq="month", symbol_batch_size=1)
    assert healthy.calls == [(("AAA",), "minute", 2023)], "only the errored batch should retry"


def test_error_chunk_is_retried_next_run_only(tmp_path):
    """A transient (Exception, not BaseException) failure is caught by
    ingest's per-chunk handler, recorded as 'error', and the loop continues
    to later chunks in the same run. The errored chunk is retried on the
    next invocation; everything already 'ok' is not."""
    con = connect(tmp_path / "warehouse.duckdb")
    symbols = ["AAA"]
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 1, tzinfo=timezone.utc)  # 2021, 2022 chunks

    flaky = FakeClient(fail_on={2021: RuntimeError("transient")})
    backfill(con, flaky, symbols, "day", start, end)
    assert flaky.calls == [(("AAA",), "day", 2021), (("AAA",), "day", 2022)]
    assert _bar_years(con) == {2022}

    healthy = FakeClient()
    backfill(con, healthy, symbols, "day", start, end)
    assert healthy.calls == [(("AAA",), "day", 2021)], "only the errored chunk should retry"
    assert _bar_years(con) == {2021, 2022}
