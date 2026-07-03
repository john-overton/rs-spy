"""Backfill orchestration: plan -> fetch -> write -> mark done.

Unit of work is (symbol, timespan, time-chunk) -- a calendar year for daily
bars, a calendar month for minute bars (minute volume is ~390x daily
volume/symbol-day, so year-sized chunks would make a single multi-symbol
Alpaca request return tens of millions of rows). Symbols within a chunk are
optionally split into smaller request batches (`symbol_batch_size`) so a
single slow/failed request doesn't block or lose progress on the whole
symbol list. A kill/crash mid-run leaves in-flight units without an
'ok'/'empty' manifest record; the next invocation re-plans and only
re-fetches those gaps (see manifest.pending_symbols).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import duckdb

from rs_spy.data import manifest
from rs_spy.data.alpaca_client import AlpacaClient
from rs_spy.data.schemas import Timespan

logger = logging.getLogger(__name__)

ChunkFreq = Literal["year", "month"]


@dataclass(frozen=True)
class YearChunk:
    year: int
    start: datetime
    end: datetime

    @property
    def unit_key(self) -> str:
        return str(self.year)


@dataclass(frozen=True)
class MonthChunk:
    year: int
    month: int
    start: datetime
    end: datetime

    @property
    def unit_key(self) -> str:
        return f"{self.year}-{self.month:02d}"


def year_chunks(start: datetime, end: datetime) -> list[YearChunk]:
    """Split [start, end) into calendar-year chunks (UTC)."""
    chunks: list[YearChunk] = []
    year = start.year
    while year <= end.year:
        chunk_start = max(start, datetime(year, 1, 1, tzinfo=timezone.utc))
        chunk_end = min(end, datetime(year + 1, 1, 1, tzinfo=timezone.utc))
        if chunk_start < chunk_end:
            chunks.append(YearChunk(year=year, start=chunk_start, end=chunk_end))
        year += 1
    return chunks


def month_chunks(start: datetime, end: datetime) -> list[MonthChunk]:
    """Split [start, end) into calendar-month chunks (UTC)."""
    chunks: list[MonthChunk] = []
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cur < end:
        nxt = (
            datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
            if cur.month == 12
            else datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)
        )
        chunk_start = max(start, cur)
        chunk_end = min(end, nxt)
        if chunk_start < chunk_end:
            chunks.append(MonthChunk(year=cur.year, month=cur.month, start=chunk_start, end=chunk_end))
        cur = nxt
    return chunks


def _batches(items: list[str], size: int | None) -> list[list[str]]:
    if not size:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _write_bars(con: duckdb.DuckDBPyConnection, df) -> None:
    if df.empty:
        return
    con.register("_new_bars", df)
    con.execute(
        """
        INSERT INTO bars
        SELECT symbol, timespan, ts, open, high, low, close, volume, vwap, trade_count
        FROM _new_bars
        ON CONFLICT (symbol, timespan, ts)
        DO UPDATE SET open = excluded.open,
                       high = excluded.high,
                       low = excluded.low,
                       close = excluded.close,
                       volume = excluded.volume,
                       vwap = excluded.vwap,
                       trade_count = excluded.trade_count
        """
    )
    con.unregister("_new_bars")


def backfill(
    con: duckdb.DuckDBPyConnection,
    client: AlpacaClient,
    symbols: list[str],
    timespan: Timespan,
    start: datetime,
    end: datetime,
    chunk_freq: ChunkFreq = "year",
    symbol_batch_size: int | None = None,
) -> None:
    """Idempotent, resumable backfill of `symbols` over [start, end) at the
    given timespan. Safe to interrupt and re-run.

    `chunk_freq="month"` + a small `symbol_batch_size` is the intended
    combination for minute bars (see module docstring); the defaults
    (year chunks, one request for all symbols) match the original M1 daily
    backfill's behavior exactly.
    """
    chunks = year_chunks(start, end) if chunk_freq == "year" else month_chunks(start, end)
    for chunk in chunks:
        unit_key = chunk.unit_key
        todo = manifest.pending_symbols(con, symbols, timespan, unit_key)
        if not todo:
            logger.info("skip %s %s: already fetched", timespan, unit_key)
            continue

        for batch in _batches(todo, symbol_batch_size):
            logger.info("fetching %s %s for %d symbols: %s", timespan, unit_key, len(batch), batch)
            try:
                df = client.fetch_bars(batch, timespan, chunk.start, chunk.end)
            except Exception as exc:  # noqa: BLE001 - deliberately broad: mark & continue
                logger.error("fetch failed for %s %s: %s", timespan, unit_key, exc)
                for sym in batch:
                    manifest.record(con, sym, timespan, unit_key, "error", error=str(exc))
                continue

            _write_bars(con, df)

            returned = set(df["symbol"].unique()) if not df.empty else set()
            for sym in batch:
                sym_rows = int((df["symbol"] == sym).sum()) if sym in returned else 0
                status = "ok" if sym_rows > 0 else "empty"
                manifest.record(con, sym, timespan, unit_key, status, row_count=sym_rows)
