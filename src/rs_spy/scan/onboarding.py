"""Most-active auto-onboarding: promote qualifying top-N most-active symbols
into the backtest symbol set (5-year daily+minute backfill into the MAIN
warehouse -- that's where backtests read).

Guards:
  * candidates are pre-filtered through the universe scan's gates (the raw
    most-actives list is dominated by ETFs and sub-$10 movers);
  * a symbol with fewer than MIN_HISTORY_DAYS daily bars (recent IPO) is
    flagged insufficient_history -- onboarded, but excluded from launched
    backtest runs until it matures (the M5 engine's SPY-derived master
    calendar means it could never truncate the shared calendar anyway; see
    the calendar-invariance test in tests/unit/test_engine_m5_backtest.py);
  * zero fetched bars in either cadence = incomplete backfill; the caller
    must not record the symbol, so the manifest retries it next night.

Maintenance / re-evaluation: nothing here re-runs itself automatically --
`onboard_symbol` is a plain function, not a scheduled job. It is, however,
already resumable (`data/ingest.py::backfill`'s manifest: 'error' units
re-fetch, 'ok'/'empty' units no-op), so simply calling it again for an
already-onboarded symbol is safe and repairs two otherwise-permanent gaps:
an `insufficient_history` symbol that has since matured (more calendar time
=> more daily bars, once the re-run's `end` reaches into a manifest year
unit not already marked done), and a partially-failed minute backfill (some
month units 'error') once the underlying outage clears. `scan/nightly.py`'s
`_run_maintenance` drives this nightly via
`data/manifest.py::symbols_with_error_units` (finds the holes) and
`store/scan_repository.py::update_onboarded` (persists the refreshed
outcome).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

from rs_spy.data.ingest import _write_bars, backfill

# algo-spec 01 §2.2: >= 300 trading days of D1 history for SMAs/ATR/RRS warm-up
MIN_HISTORY_DAYS = 300


@dataclass(frozen=True)
class OnboardingOutcome:
    symbol: str
    history_start: date | None
    n_daily_bars: int
    n_minute_bars: int
    insufficient_history: bool


def select_onboarding_candidates(
    most_actives_payload: dict,
    *,
    passing: set[str],
    curated: set[str],
    onboarded: set[str],
    top_n: int = 10,
) -> list[str]:
    """Top-N most-active symbols that pass the scan and aren't already known.
    Order-preserving (most active first), deduplicated.

    The `top_n` cap applies to the RAW most-actives ranking BEFORE gate
    filtering, per the design spec (docs/superpowers/specs/
    2026-07-05-universe-scan-design.md, "Most-active auto-onboarding": the
    candidate pool is the day's raw top-N; gates filter within it). We onboard
    only names genuinely in the day's top-N activity, so a night where the raw
    top-N is all ETFs/sub-$10 movers correctly yields zero candidates. Do not
    "fix" this to filter-then-cap.
    """
    entries = (most_actives_payload.get("most_actives") or [])[:top_n]
    out: list[str] = []
    for entry in entries:
        sym = entry.get("symbol")
        if not sym or sym in out:
            continue
        if sym in passing and sym not in curated and sym not in onboarded:
            out.append(sym)
    return out


def onboard_symbol(
    con: duckdb.DuckDBPyConnection,
    client,
    symbol: str,
    end: datetime,
    years: int = 5,
) -> OnboardingOutcome:
    """Backfill `symbol`'s daily (year chunks) and minute (month chunks) bars
    into `con` (the MAIN warehouse) and report what landed. Resumable: a
    partial failure leaves 'error' manifest units that retry next run.

    Interpreting the outcome: callers must check
    `n_daily_bars == 0 or n_minute_bars == 0` (incomplete backfill -- do NOT
    record the symbol as onboarded) BEFORE interpreting `insufficient_history`;
    the flag is True both for a failed daily backfill (zero bars) and for a
    short-but-real history (recent IPO)."""
    start = end - timedelta(days=365 * years + 5)
    backfill(con, client, [symbol], "day", start, end, chunk_freq="year")
    backfill(con, client, [symbol], "minute", start, end, chunk_freq="month")

    # `backfill`'s manifest units are calendar year (daily) / month (minute),
    # marked 'ok' as soon as a fetch for that unit succeeds -- even when `end`
    # fell mid-period. `pending_symbols` never revisits an 'ok' unit, so a
    # symbol onboarded mid-year/mid-month would otherwise permanently miss
    # bars for the rest of that period on later maintenance runs. Tail-heal
    # both cadences from the newest stored bar each visit (mirrors
    # scan/bars.py::refresh_daily_bars's tail stage); a cheap no-op when
    # there's nothing new to fetch.
    for timespan in ("day", "minute"):
        newest = con.execute(
            "SELECT max(ts) FROM bars WHERE symbol = ? AND timespan = ?",
            [symbol, timespan],
        ).fetchone()[0]
        if newest is not None:
            newest_dt = pd.Timestamp(newest).tz_localize("UTC").to_pydatetime()
            if newest_dt < end:
                _write_bars(con, client.fetch_bars([symbol], timespan, newest_dt, end))

    first_day, n_daily = con.execute(
        "SELECT CAST(min(ts) AS DATE), count(*) FROM bars "
        "WHERE symbol = ? AND timespan = 'day'",
        [symbol],
    ).fetchone()
    n_minute = con.execute(
        "SELECT count(*) FROM bars WHERE symbol = ? AND timespan = 'minute'",
        [symbol],
    ).fetchone()[0]
    n_daily, n_minute = int(n_daily), int(n_minute)
    return OnboardingOutcome(
        symbol=symbol,
        history_start=first_day,
        n_daily_bars=n_daily,
        n_minute_bars=n_minute,
        insufficient_history=n_daily < MIN_HISTORY_DAYS,
    )
