# M9: Nightly Universe Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the spec (`docs/superpowers/specs/2026-07-05-universe-scan-design.md`): a nightly universe scan of the whole US equity market (algo-spec 01 §4) computed from broad Alpaca daily bars, with a screener-endpoint snapshot recorder and a most-active auto-onboarding pipeline (gate-filter → 5-year minute backfill → tagged backtest re-run).

**Architecture:** Broad daily bars live in a **separate DuckDB file** (`data/scan.duckdb`) with the same `bars`/`fetch_manifest` schema as the main warehouse, so the curated warehouse and every existing loader/backtest stay untouched. The scan itself is one code path for live and point-in-time: `run_universe_scan(con, assets, as_of, config)` reads only cached bars ≤ `as_of`. Snapshots/onboarding records go to the existing Postgres runs-store; onboarding backfills go into the **main** warehouse (that's where backtests read); the tagged re-run goes through the existing detached job runner via a new inert `BacktestConfigM5.extra_symbols` field.

**Tech Stack:** Python 3.14, alpaca-py 0.43 (`TradingClient` assets, `ScreenerClient` most-actives/movers), DuckDB, pandas, psycopg3, typer, pytest (+testcontainers for PG), ruff.

**Milestone naming:** this is **M9** (M8 is the already-specced, not-yet-built Backtest UI — `docs/superpowers/specs/backtest-ui.md`). Commit messages prefixed `M9:`.

## Global Constraints

- Zero behavior change to the existing curated-universe pipeline: `python -m pytest -q` (251 tests) stays green and the default M5 backtest reproduces bit-for-bit after every task. The main `bars` table is written only by the onboarding path (which is additive rows for new symbols).
- All unit tests hermetic: no network, no credentials, no real warehouse. In-memory DuckDB (`:memory:`) is allowed (it's local); Postgres tests use the existing testcontainers fixtures and auto-skip without Docker.
- Feed switch from day one: every liquidity threshold comes from `ScanConfig`, with `iex` and `sip` presets. No hardcoded thresholds outside `scan/config.py`.
- RTH-only policy: daily bars are session aggregates already; onboarded minute data is consumed through the existing `rth_only=True` loader convention. No pre/post-market-derived values enter scan or onboarding math.
- No-lookahead: `run_universe_scan(as_of=t)` may only depend on bars with date ≤ t (tested, not assumed).
- "Document, don't silently approximate": ETF/name-heuristic imperfection, the float-gate substitution, the dropped halt gate, and survivorship limits get docstrings.
- `ruff check .` (line-length 100) clean before every commit. Run tests from repo root with `source .venv/bin/activate`.

## File structure

```
src/rs_spy/
  data/alpaca_client.py      MODIFY  + fetch_assets(), fetch_screener_snapshots(), TradingClient/ScreenerClient wiring
  config.py                  MODIFY  + scan_warehouse_path / resolved_scan_warehouse_path()
  scan/__init__.py           CREATE  package docstring (purpose + disclosed limits)
  scan/config.py             CREATE  ScanConfig + iex/sip presets + heuristic lists
  scan/engine.py             CREATE  compute_scan_metrics (SQL), apply_gates, run_universe_scan, ScanResult, ScanCoverageError
  scan/bars.py               CREATE  connect_scan(), refresh_daily_bars() (initial 5y + self-healing tail)
  scan/onboarding.py         CREATE  select_onboarding_candidates(), onboard_symbol(), OnboardingOutcome
  scan/nightly.py            CREATE  run_nightly() orchestration + NightlyReport
  store/schema.py            MODIFY  + scan_runs, universe_snapshots, screener_snapshots, onboarded_symbols DDL
  store/scan_repository.py   CREATE  save_scan, get_universe_snapshot, save/get_screener_snapshot, record_onboarded, list_onboarded
  backtest/engine_m5.py      MODIFY  + BacktestConfigM5.extra_symbols (inert in engine)
  store/serialize.py         MODIFY  extra_symbols tuple round-trip
  jobs/runner.py             MODIFY  _trade_symbols() merge + UNKNOWN-sector default
scripts/run_nightly_scan.py  CREATE  typer CLI + cron/launchd docs
tests/unit/test_alpaca_client_scan.py   CREATE
tests/unit/test_scan_config.py          CREATE
tests/unit/test_scan_engine.py          CREATE
tests/unit/test_scan_bars.py            CREATE
tests/unit/test_scan_onboarding.py      CREATE
tests/unit/test_serialize_extra_symbols.py  CREATE (or append to test_store_serialize.py)
tests/unit/test_engine_m5_backtest.py   MODIFY  calendar-invariance guard test
tests/unit/test_jobs_symbols.py         CREATE
tests/integration/conftest.py           MODIFY  truncate new tables
tests/integration/test_scan_repository.py   CREATE
tests/integration/test_nightly_scan.py      CREATE
```

---

### Task 1: Alpaca client — assets + screener snapshots

**Files:**
- Modify: `src/rs_spy/data/alpaca_client.py`
- Test: `tests/unit/test_alpaca_client_scan.py`

**Interfaces:**
- Produces: `AlpacaClient.fetch_assets() -> pd.DataFrame` with columns `ASSET_COLUMNS = ["symbol", "name", "exchange", "tradable", "shortable", "fractionable", "optionable"]` (exchange as plain string, optionable derived from the `attributes` list).
- Produces: `AlpacaClient.fetch_screener_snapshots(top_actives: int = 100, top_movers: int = 50) -> dict[str, dict]` with keys `"most_actives_volume"`, `"most_actives_trades"`, `"market_movers"`, each a JSON-safe dict (`model_dump(mode="json")`). The most-actives payloads have a `"most_actives"` list of `{"symbol", "volume", "trade_count"}` dicts — Task 6's candidate selection consumes exactly that shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_alpaca_client_scan.py`:

```python
"""fetch_assets / fetch_screener_snapshots -- vendor-response normalization.

Hermetic: the underlying alpaca-py clients are replaced with stubs; only the
row-shaping logic in our wrapper is under test.
"""
from types import SimpleNamespace

from rs_spy.config import Settings
from rs_spy.data.alpaca_client import ASSET_COLUMNS, AlpacaClient


def _client() -> AlpacaClient:
    return AlpacaClient(Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"))


def _asset(symbol, name, exchange, tradable=True, attributes=None):
    return SimpleNamespace(
        symbol=symbol, name=name, exchange=SimpleNamespace(value=exchange),
        tradable=tradable, shortable=True, fractionable=True, attributes=attributes,
    )


def test_fetch_assets_normalizes_vendor_objects_to_a_dataframe():
    client = _client()
    client._trading_client = SimpleNamespace(
        get_all_assets=lambda request: [
            _asset("AAPL", "Apple Inc. Common Stock", "NASDAQ", attributes=["has_options"]),
            _asset("XYZ", "Xyz Corp", "NYSE", attributes=None),
            _asset("NOPE", "NoTrade Inc", "NYSE", tradable=False, attributes=[]),
        ]
    )
    df = client.fetch_assets()
    assert list(df.columns) == ASSET_COLUMNS
    assert df.loc[df.symbol == "AAPL", "optionable"].item() is True
    assert df.loc[df.symbol == "XYZ", "optionable"].item() is False  # attributes=None tolerated
    assert df.loc[df.symbol == "AAPL", "exchange"].item() == "NASDAQ"  # enum -> plain string
    assert df.loc[df.symbol == "NOPE", "tradable"].item() is False


def test_fetch_screener_snapshots_returns_three_json_safe_payloads():
    client = _client()
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self, mode="json"):
            return self._payload

    def fake_most_actives(request):
        calls.append(("most_actives", request.by, request.top))
        return FakeResponse({"most_actives": [{"symbol": "HOOD", "volume": 1e8, "trade_count": 9e5}]})

    def fake_movers(request):
        calls.append(("movers", request.top))
        return FakeResponse({"gainers": [], "losers": [], "market_type": "stocks"})

    client._screener_client = SimpleNamespace(
        get_most_actives=fake_most_actives, get_market_movers=fake_movers
    )
    out = client.fetch_screener_snapshots(top_actives=100, top_movers=50)
    assert set(out) == {"most_actives_volume", "most_actives_trades", "market_movers"}
    assert out["most_actives_volume"]["most_actives"][0]["symbol"] == "HOOD"
    # one most-actives call per ranking metric, with the requested tops
    kinds = [c[0] for c in calls]
    assert kinds.count("most_actives") == 2 and kinds.count("movers") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_alpaca_client_scan.py -v`
Expected: FAIL — `ImportError: cannot import name 'ASSET_COLUMNS'`.

- [ ] **Step 3: Implement**

In `src/rs_spy/data/alpaca_client.py`, add imports after the existing alpaca imports:

```python
from alpaca.data.enums import MostActivesBy
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import MarketMoversRequest, MostActivesRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest
```

Add below `BAR_COLUMNS`:

```python
ASSET_COLUMNS = [
    "symbol",
    "name",
    "exchange",
    "tradable",
    "shortable",
    "fractionable",
    "optionable",
]

# Alpaca marks option availability as an entry in Asset.attributes; the exact
# label has drifted across API versions, so accept both known spellings.
_OPTION_ATTRIBUTES = {"options_enabled", "has_options"}
```

In `AlpacaClient.__init__`, after `self._client = StockHistoricalDataClient(...)`:

```python
        self._trading_client = TradingClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            paper=True,
        )
        self._screener_client = ScreenerClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
```

Add two methods to `AlpacaClient` (after `fetch_bars`):

```python
    def fetch_assets(self) -> pd.DataFrame:
        """All active US-equity assets, normalized to ASSET_COLUMNS.

        Alpaca has no security-type field (common stock vs ETF/ADR are all
        `us_equity`) and no shares float -- the scan's listing gate works from
        name/exchange heuristics instead (see scan/config.py).
        """
        self._limiter.acquire()
        assets = self._trading_client.get_all_assets(
            GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        )
        rows = []
        for a in assets:
            attributes = set(a.attributes or [])
            rows.append(
                {
                    "symbol": a.symbol,
                    "name": a.name or "",
                    "exchange": str(getattr(a.exchange, "value", a.exchange)),
                    "tradable": bool(a.tradable),
                    "shortable": bool(a.shortable),
                    "fractionable": bool(a.fractionable),
                    "optionable": bool(attributes & _OPTION_ATTRIBUTES),
                }
            )
        return pd.DataFrame(rows, columns=ASSET_COLUMNS)

    def fetch_screener_snapshots(
        self, top_actives: int = 100, top_movers: int = 50
    ) -> dict[str, dict]:
        """Live screener snapshots (most-actives by volume/trades, movers).

        These endpoints are REAL-TIME ONLY (no as-of parameter exists) -- every
        day not captured is lost forever, hence the nightly recorder. Payloads
        are raw model_dump(mode="json") dicts, stored verbatim as JSONB.
        """
        out: dict[str, dict] = {}
        self._limiter.acquire()
        out["most_actives_volume"] = self._screener_client.get_most_actives(
            MostActivesRequest(by=MostActivesBy.VOLUME, top=top_actives)
        ).model_dump(mode="json")
        self._limiter.acquire()
        out["most_actives_trades"] = self._screener_client.get_most_actives(
            MostActivesRequest(by=MostActivesBy.TRADES, top=top_actives)
        ).model_dump(mode="json")
        self._limiter.acquire()
        out["market_movers"] = self._screener_client.get_market_movers(
            MarketMoversRequest(top=top_movers)
        ).model_dump(mode="json")
        return out
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_alpaca_client_scan.py -v && python -m pytest -q && ruff check .`
Expected: new tests PASS, all 251+ green, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/data/alpaca_client.py tests/unit/test_alpaca_client_scan.py
git commit -m "M9: AlpacaClient.fetch_assets + fetch_screener_snapshots"
```

---

### Task 2: Scan warehouse — separate DuckDB file + self-healing daily refresh

**Files:**
- Modify: `src/rs_spy/config.py`
- Create: `src/rs_spy/scan/__init__.py`, `src/rs_spy/scan/bars.py`
- Test: `tests/unit/test_scan_bars.py`

**Interfaces:**
- Consumes: `rs_spy.data.ingest.backfill/_batches/_write_bars`, `rs_spy.data.warehouse.connect`, `AlpacaClient.fetch_bars(symbols, "day", start, end)`.
- Produces: `Settings.resolved_scan_warehouse_path() -> Path` (default `data/scan.duckdb`); `scan.bars.connect_scan(path, read_only=False) -> duckdb.DuckDBPyConnection`; `scan.bars.refresh_daily_bars(con, client, symbols, end, *, years=5, tail_days=7, symbol_batch_size=200) -> None`.

Background: the manifest marks a calendar-year unit done after its first fetch, so the *current* year's unit goes stale as new days close (the same latent staleness exists in `scripts/backfill_daily.py` — out of scope here, but the tail refresh below is the pattern that fixes it). `refresh_daily_bars` therefore runs the manifest-driven historical backfill first, then **unconditionally** re-fetches a recent tail and upserts it. The tail start self-heals: it is the older of (`end - tail_days`) and the newest bar already stored, so a job that hasn't run for weeks catches up in one pass.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_scan_bars.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scan_bars.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rs_spy.scan'` (and the Settings test fails with `AttributeError: resolved_scan_warehouse_path` once the module exists).

- [ ] **Step 3: Implement**

In `src/rs_spy/config.py`, add after `warehouse_path: Path | None = None`:

```python
    # Broad-scan daily-bar warehouse (M9). A SEPARATE DuckDB file from the
    # curated warehouse so ~11k-symbol scan data can never bleed into
    # curated-universe queries, and the scan's nightly read-write connection
    # never contends with concurrent read-only backtests on warehouse.duckdb.
    scan_warehouse_path: Path | None = None
```

and after `resolved_warehouse_path`:

```python
    def resolved_scan_warehouse_path(self) -> Path:
        return self.scan_warehouse_path or (self.data_dir / "scan.duckdb")
```

Create `src/rs_spy/scan/__init__.py`:

```python
"""Nightly universe scan (algo-spec 01 §4) -- the "what to trade" discovery half.

Self-computed from broad Alpaca daily bars so one code path serves both the
live nightly scan (as_of=today) and point-in-time reconstruction (as_of=any
cached date). Spec: docs/superpowers/specs/2026-07-05-universe-scan-design.md.

Disclosed limits (deliberate, documented, not silent):
  * No security-type or float data from Alpaca: ETF exclusion is a
    name/exchange heuristic; the float>=50M gate is substituted by the
    dollar-volume floor (see scan/config.py).
  * The halt-history gate (01 §4.5) is dropped -- no historical halt feed.
  * Point-in-time reconstruction uses the CURRENT asset list (survivorship
    bias: symbols delisted before today are absent).
  * Free-tier IEX volume is ~2-3% of consolidated SIP volume; ScanConfig
    carries per-feed thresholds so a paid SIP upgrade is config, not code.
"""
```

Create `src/rs_spy/scan/bars.py`:

```python
"""Broad-scan daily-bar storage + refresh.

A separate DuckDB file (Settings.resolved_scan_warehouse_path, default
data/scan.duckdb) with the exact same bars/fetch_manifest schema as the main
warehouse -- warehouse.connect() is reused as-is.

Refresh strategy: the manifest-driven backfill covers history idempotently,
but a calendar-year manifest unit is marked done at first fetch and goes
stale as the current year grows. refresh_daily_bars therefore always
re-fetches a recent tail unconditionally and upserts it (bars upserts are
idempotent). The tail start self-heals to the newest stored bar, so a run
after any outage catches up in one pass.
"""
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from rs_spy.data.ingest import _batches, _write_bars, backfill
from rs_spy.data.warehouse import connect


def connect_scan(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the scan warehouse (same schema/DDL as the main warehouse)."""
    return connect(path, read_only=read_only)


def refresh_daily_bars(
    con: duckdb.DuckDBPyConnection,
    client,
    symbols: list[str],
    end: datetime,
    *,
    years: int = 5,
    tail_days: int = 7,
    symbol_batch_size: int = 200,
) -> None:
    """Bring the scan warehouse's daily bars up to date through `end`.

    1. Manifest-driven historical backfill over [end - years, end) -- cheap
       no-op for every already-done (symbol, year) unit.
    2. Unconditional tail re-fetch from min(end - tail_days, newest stored
       bar) -- picks up days the current-year manifest unit can't see.
    """
    start = end - timedelta(days=365 * years + 5)
    backfill(
        con, client, symbols, "day", start, end,
        chunk_freq="year", symbol_batch_size=symbol_batch_size,
    )

    tail_start = end - timedelta(days=tail_days)
    latest = con.execute("SELECT max(ts) FROM bars WHERE timespan = 'day'").fetchone()[0]
    if latest is not None:
        latest_dt = pd.Timestamp(latest).tz_localize("UTC").to_pydatetime()
        tail_start = min(tail_start, latest_dt)
    for batch in _batches(symbols, symbol_batch_size):
        df = client.fetch_bars(batch, "day", tail_start, end)
        _write_bars(con, df)
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_scan_bars.py -v && python -m pytest -q && ruff check .`
Expected: PASS, suite green, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/config.py src/rs_spy/scan/__init__.py src/rs_spy/scan/bars.py tests/unit/test_scan_bars.py
git commit -m "M9: scan warehouse (separate DuckDB) + self-healing daily refresh"
```

---

### Task 3: ScanConfig + gate logic (pure)

**Files:**
- Create: `src/rs_spy/scan/config.py`, `src/rs_spy/scan/engine.py` (gates half)
- Test: `tests/unit/test_scan_config.py`, `tests/unit/test_scan_engine.py` (gates half)

**Interfaces:**
- Produces: `ScanConfig` (frozen dataclass; fields below) and `ScanConfig.for_feed("iex"|"sip")`.
- Produces: `scan.engine.GATE_ORDER = ("listing", "coverage", "price", "adv_shares", "adv_dollars")` and `apply_gates(assets: pd.DataFrame, metrics: pd.DataFrame, config: ScanConfig) -> tuple[pd.DataFrame, dict[str, int]]`. `assets` has Task 1's `ASSET_COLUMNS`; `metrics` is indexed by symbol with columns `last_close, last_bar_date, adv_shares, adv_dollars, n_bars` (Task 4 produces it). The returned `evaluated` frame is indexed by symbol with the joined columns plus `passed: bool` and `first_fail: str|None`; the funnel dict has keys `assets`, `fail_<gate>` for each gate, `passed`, and partitions exactly.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_scan_config.py`:

```python
from rs_spy.scan.config import ScanConfig


def test_iex_defaults_are_recalibrated_proxies():
    c = ScanConfig.for_feed("iex")
    # IEX volume is ~2-3% of consolidated SIP volume (see IMPLEMENTATION.md's
    # ADV-gate recalibration); these are proxies for the spec's 1M sh / $25M.
    assert c.feed == "iex"
    assert c.min_adv_shares < 100_000
    assert c.min_adv_dollars < 5_000_000
    assert c.min_price == 10.0
    assert c.adv_window == 20


def test_sip_preset_uses_the_spec_thresholds_verbatim():
    c = ScanConfig.for_feed("sip")
    assert c.feed == "sip"
    assert c.min_adv_shares == 1_000_000
    assert c.min_adv_dollars == 25_000_000


def test_unknown_feed_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        ScanConfig.for_feed("polygon")
```

Create `tests/unit/test_scan_engine.py`:

```python
"""Gate logic golden tests + funnel partition. Pure pandas -- no DuckDB here."""
import pandas as pd
import pytest

from rs_spy.data.alpaca_client import ASSET_COLUMNS
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import GATE_ORDER, apply_gates

CFG = ScanConfig()  # iex defaults: min_price=10, adv_window=20


def _assets(rows):
    return pd.DataFrame(rows, columns=ASSET_COLUMNS)


def _asset_row(symbol, name="Good Corp Common Stock", exchange="NYSE", tradable=True):
    return {"symbol": symbol, "name": name, "exchange": exchange, "tradable": tradable,
            "shortable": True, "fractionable": True, "optionable": True}


def _metrics(entries):
    """entries: {symbol: (last_close, adv_shares, adv_dollars, n_bars)}"""
    df = pd.DataFrame(
        [
            {"symbol": s, "last_close": c, "last_bar_date": pd.Timestamp("2026-07-02"),
             "adv_shares": sh, "adv_dollars": d, "n_bars": n}
            for s, (c, sh, d, n) in entries.items()
        ]
    )
    return df.set_index("symbol")


GOOD = (50.0, CFG.min_adv_shares * 2, CFG.min_adv_dollars * 2, 20)


def test_each_gate_fails_exactly_the_symbol_built_to_fail_it():
    assets = _assets([
        _asset_row("PASS"),
        _asset_row("NOTRADE", tradable=False),
        _asset_row("ARCAETF", exchange="ARCA"),
        _asset_row("SPYLIKE", name="SPDR S&P 500 ETF Trust"),
        _asset_row("CHEAP"),
        _asset_row("THINVOL"),
        _asset_row("LOWDOLL"),
        _asset_row("YOUNG"),
    ])
    metrics = _metrics({
        "PASS": GOOD,
        "NOTRADE": GOOD,
        "ARCAETF": GOOD,
        "SPYLIKE": GOOD,
        "CHEAP": (9.99, GOOD[1], GOOD[2], 20),
        "THINVOL": (50.0, CFG.min_adv_shares / 2, GOOD[2], 20),
        "LOWDOLL": (50.0, GOOD[1], CFG.min_adv_dollars / 2, 20),
        "YOUNG": (50.0, GOOD[1], GOOD[2], 19),
    })
    ev, funnel = apply_gates(assets, metrics, CFG)
    assert ev.loc["PASS", "passed"] and ev.loc["PASS", "first_fail"] is None
    assert ev.loc["NOTRADE", "first_fail"] == "listing"
    assert ev.loc["ARCAETF", "first_fail"] == "listing"
    assert ev.loc["SPYLIKE", "first_fail"] == "listing"
    assert ev.loc["CHEAP", "first_fail"] == "price"
    assert ev.loc["THINVOL", "first_fail"] == "adv_shares"
    assert ev.loc["LOWDOLL", "first_fail"] == "adv_dollars"
    assert ev.loc["YOUNG", "first_fail"] == "coverage"


def test_ten_dollar_boundary_is_inclusive():
    assets = _assets([_asset_row("ATTEN")])
    ev, _ = apply_gates(assets, _metrics({"ATTEN": (10.0, GOOD[1], GOOD[2], 20)}), CFG)
    assert ev.loc["ATTEN", "passed"]


def test_reit_trust_names_are_not_blocked_but_etf_issuers_are():
    # "Trust" alone must NOT be in the blocklist: Camden Property Trust is a
    # legitimate S&P 500 common stock. ETF issuer brands + the word ETF are.
    assets = _assets([
        _asset_row("CPT", name="Camden Property Trust"),
        _asset_row("FAKE1", name="iShares Core Whatever"),
        _asset_row("FAKE2", name="ProShares UltraPro Something", exchange="NASDAQ"),
        _asset_row("QQQ", name="Invesco QQQ Trust, Series 1", exchange="NASDAQ"),
        _asset_row("BRK.B", name="Berkshire Hathaway Inc. Class B"),
        _asset_row("WTS.WS", name="Some Warrant"),
    ])
    metrics = _metrics({s: GOOD for s in ["CPT", "FAKE1", "FAKE2", "QQQ", "BRK.B", "WTS.WS"]})
    ev, _ = apply_gates(assets, metrics, CFG)
    assert ev.loc["CPT", "passed"]
    assert ev.loc["BRK.B", "passed"]  # class shares survive the suffix check
    assert ev.loc["FAKE1", "first_fail"] == "listing"
    assert ev.loc["FAKE2", "first_fail"] == "listing"
    assert ev.loc["QQQ", "first_fail"] == "listing"  # explicit symbol denylist
    assert ev.loc["WTS.WS", "first_fail"] == "listing"  # warrant suffix


def test_symbol_missing_from_metrics_fails_coverage_not_a_crash():
    assets = _assets([_asset_row("NEWIPO")])
    ev, funnel = apply_gates(assets, _metrics({}), CFG)
    assert ev.loc["NEWIPO", "first_fail"] == "coverage"


def test_funnel_partitions_exactly():
    assets = _assets([
        _asset_row("PASS"), _asset_row("NOTRADE", tradable=False), _asset_row("CHEAP"),
    ])
    metrics = _metrics({"PASS": GOOD, "NOTRADE": GOOD, "CHEAP": (5.0, GOOD[1], GOOD[2], 20)})
    ev, funnel = apply_gates(assets, metrics, CFG)
    assert funnel["assets"] == 3
    assert funnel["assets"] == funnel["passed"] + sum(funnel[f"fail_{g}"] for g in GATE_ORDER)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scan_config.py tests/unit/test_scan_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rs_spy.scan.config'`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/scan/config.py`:

```python
"""Scan thresholds + listing heuristics (algo-spec 01 §4, with disclosed substitutions).

Feed presets: `sip` uses the spec's real thresholds (1M shares / $25M); `iex`
uses recalibrated proxies for the free tier's IEX-only volume (~2-3% of
consolidated -- same evidence base as BacktestConfigM5.min_adv_shares=50k).
The IEX defaults below are pre-calibration estimates; Task 9 calibrates them
against real cached data and updates them (with the measured numbers in a
comment) if the resulting universe size is far outside the spec's 800-1,500.

Heuristic listing filters (Alpaca has no security-type field):
  * exchange allowlist NYSE/NASDAQ/AMEX -- ARCA/BATS listings are
    overwhelmingly ETFs/ETNs;
  * name patterns for ETF words and pure-ETF issuer brands. "Trust" alone is
    deliberately NOT blocked (Camden Property Trust and other REITs are real
    common stocks); NASDAQ-listed ETFs that dodge the issuer patterns are
    caught case-by-case via symbol_denylist (QQQ today; extend as found --
    the universe_snapshots table makes any slip visible);
  * symbol suffixes for warrants/units/rights.
The float>=50M gate (01 §4.4) is SUBSTITUTED by the dollar-volume floor (no
float data on Alpaca); the halt-history gate (01 §4.5) is DROPPED (no
historical halt feed). Both disclosed in the spec and scan/__init__.py.
"""
from dataclasses import dataclass

IEX_MIN_ADV_SHARES = 30_000.0
IEX_MIN_ADV_DOLLARS = 750_000.0
SIP_MIN_ADV_SHARES = 1_000_000.0
SIP_MIN_ADV_DOLLARS = 25_000_000.0

DEFAULT_EXCHANGE_ALLOWLIST = frozenset({"NYSE", "NASDAQ", "AMEX"})
DEFAULT_NAME_BLOCKLIST = (
    r"\bETF\b",
    r"\bETN\b",
    r"\bFund\b",
    r"\bIndex\b",
    "iShares",
    "ProShares",
    "SPDR",
    "Direxion",
    "Vanguard",
)
DEFAULT_SYMBOL_DENYLIST = frozenset({"QQQ"})
DEFAULT_SYMBOL_SUFFIX_BLOCKLIST = (".WS", ".U", ".RT")


@dataclass(frozen=True)
class ScanConfig:
    feed: str = "iex"
    min_price: float = 10.0
    adv_window: int = 20
    min_adv_shares: float = IEX_MIN_ADV_SHARES
    min_adv_dollars: float = IEX_MIN_ADV_DOLLARS
    exchange_allowlist: frozenset = DEFAULT_EXCHANGE_ALLOWLIST
    name_blocklist: tuple = DEFAULT_NAME_BLOCKLIST
    symbol_denylist: frozenset = DEFAULT_SYMBOL_DENYLIST
    symbol_suffix_blocklist: tuple = DEFAULT_SYMBOL_SUFFIX_BLOCKLIST
    min_coverage_fraction: float = 0.80

    @classmethod
    def for_feed(cls, feed: str) -> "ScanConfig":
        if feed == "iex":
            return cls()
        if feed == "sip":
            return cls(
                feed="sip",
                min_adv_shares=SIP_MIN_ADV_SHARES,
                min_adv_dollars=SIP_MIN_ADV_DOLLARS,
            )
        raise ValueError(f"unknown feed {feed!r}: expected 'iex' or 'sip'")
```

Create `src/rs_spy/scan/engine.py` (gates half; Task 4 appends the SQL/scan half):

```python
"""Universe-scan engine: per-symbol metrics (SQL, Task 4) + gate application.

Gate evaluation is first-fail attributed in GATE_ORDER so the funnel
partitions exactly: every evaluated symbol lands in exactly one of
fail_<gate> or passed (tested by the funnel-partition test).
"""
import pandas as pd

from rs_spy.scan.config import ScanConfig

GATE_ORDER = ("listing", "coverage", "price", "adv_shares", "adv_dollars")


def apply_gates(
    assets: pd.DataFrame, metrics: pd.DataFrame, config: ScanConfig
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join asset metadata with as-of metrics and apply 01 §4's gates.

    Returns (evaluated, funnel): `evaluated` indexed by symbol with a bool
    `passed` and a `first_fail` gate name (None when passed); `funnel` counts
    every symbol exactly once.
    """
    ev = assets.set_index("symbol").join(metrics, how="left")
    sym = ev.index.to_series()

    name_pattern = "|".join(f"(?:{p})" for p in config.name_blocklist)
    listing_ok = (
        ev["tradable"].fillna(False)
        & ev["exchange"].isin(config.exchange_allowlist)
        & ~ev["name"].fillna("").str.contains(name_pattern, case=False, regex=True)
        & ~sym.str.endswith(tuple(config.symbol_suffix_blocklist))
        & ~sym.isin(config.symbol_denylist)
    )
    gate_ok = {
        "listing": listing_ok,
        "coverage": ev["n_bars"].fillna(0) >= config.adv_window,
        "price": (ev["last_close"] >= config.min_price).fillna(False),
        "adv_shares": (ev["adv_shares"] >= config.min_adv_shares).fillna(False),
        "adv_dollars": (ev["adv_dollars"] >= config.min_adv_dollars).fillna(False),
    }

    first_fail = pd.Series(None, index=ev.index, dtype=object)
    remaining = pd.Series(True, index=ev.index)
    funnel: dict[str, int] = {"assets": int(len(ev))}
    for gate in GATE_ORDER:
        failed_here = remaining & ~gate_ok[gate]
        first_fail[failed_here] = gate
        funnel[f"fail_{gate}"] = int(failed_here.sum())
        remaining &= gate_ok[gate]
    ev["passed"] = remaining
    ev["first_fail"] = first_fail
    funnel["passed"] = int(remaining.sum())
    return ev, funnel
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_scan_config.py tests/unit/test_scan_engine.py -v && python -m pytest -q && ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/scan/config.py src/rs_spy/scan/engine.py tests/unit/test_scan_config.py tests/unit/test_scan_engine.py
git commit -m "M9: ScanConfig (iex/sip presets) + first-fail gate application"
```

---

### Task 4: As-of metrics SQL + `run_universe_scan` (no-lookahead + coverage refusal)

**Files:**
- Modify: `src/rs_spy/scan/engine.py`
- Test: `tests/unit/test_scan_engine.py` (append)

**Interfaces:**
- Consumes: Task 2's `connect_scan` (tests), Task 3's `apply_gates`/`ScanConfig`.
- Produces: `compute_scan_metrics(con, as_of, adv_window=20) -> pd.DataFrame` (indexed by symbol: `last_close, last_bar_date (pd.Timestamp), adv_shares, adv_dollars, n_bars`); `ScanCoverageError(RuntimeError)`; `ScanResult` dataclass (`as_of, evaluated, funnel`, property `passing -> list[str]` sorted); `run_universe_scan(con, assets, as_of, config=None) -> ScanResult`.

Note on ADV semantics: the trailing window is the symbol's **last `adv_window` bars** at or before `as_of`, not calendar days — for thin IEX symbols with missing days this is the symbol's own last 20 trading prints, a documented approximation consistent with how the IEX feed works.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scan_engine.py`:

```python
# ---------------------------------------------------------------- as-of / SQL half
from pathlib import Path

from rs_spy.data.ingest import _write_bars
from rs_spy.scan.bars import connect_scan
from rs_spy.scan.engine import ScanCoverageError, compute_scan_metrics, run_universe_scan


def _bar_frame(symbol, dates, close=50.0, volume=100_000):
    return pd.DataFrame(
        {
            "symbol": symbol, "timespan": "day",
            "ts": pd.DatetimeIndex(dates, tz="UTC"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "vwap": close, "trade_count": 100,
        }
    )


def _seeded_con(frames):
    con = connect_scan(Path(":memory:"))
    for f in frames:
        _write_bars(con, f)
    return con


DAYS = pd.bdate_range("2026-05-01", periods=30)


def test_compute_scan_metrics_uses_only_bars_at_or_before_as_of():
    con = _seeded_con([_bar_frame("AAA", DAYS, close=50.0, volume=100_000)])
    as_of = DAYS[19]  # bar #20 of 30
    m = compute_scan_metrics(con, as_of, adv_window=20)
    assert m.loc["AAA", "n_bars"] == 20
    assert m.loc["AAA", "last_bar_date"] == as_of


def test_no_lookahead_future_bars_do_not_change_the_scan():
    """The spec's 'no future bias' guarantee, tested the same way the
    indicator causality tests work: truncate vs full history, same answer."""
    full = _seeded_con([_bar_frame("AAA", DAYS)])
    truncated = _seeded_con([_bar_frame("AAA", DAYS[:20])])
    as_of = DAYS[19]
    m_full = compute_scan_metrics(full, as_of, adv_window=20)
    m_trunc = compute_scan_metrics(truncated, as_of, adv_window=20)
    pd.testing.assert_frame_equal(m_full, m_trunc)


def test_adv_uses_the_trailing_window_only():
    # 40 bars: first 20 at volume 1M, last 20 at volume 10k. As of the end,
    # ADV must reflect only the trailing 20 bars.
    days = pd.bdate_range("2026-04-01", periods=40)
    f = pd.concat([
        _bar_frame("AAA", days[:20], volume=1_000_000),
        _bar_frame("AAA", days[20:], volume=10_000),
    ])
    con = _seeded_con([f])
    m = compute_scan_metrics(con, days[-1], adv_window=20)
    assert m.loc["AAA", "adv_shares"] == 10_000


def test_run_universe_scan_end_to_end_pass_and_coverage_refusal():
    assets = _assets([_asset_row("AAA"), _asset_row("BBB")])
    con = _seeded_con([
        _bar_frame("AAA", DAYS, close=50.0, volume=int(CFG.min_adv_shares * 2)),
        _bar_frame("BBB", DAYS, close=50.0, volume=int(CFG.min_adv_shares * 2)),
    ])
    result = run_universe_scan(con, assets, DAYS[-1], CFG)
    assert result.passing == ["AAA", "BBB"]
    assert result.funnel["passed"] == 2

    # a non-trading date (weekend after DAYS[-1]) -> no symbol has an as-of
    # bar -> the scan must refuse rather than emit a stale/empty snapshot
    weekend = DAYS[-1] + pd.Timedelta(days=2)
    assert weekend.dayofweek in (5, 6)
    with pytest.raises(ScanCoverageError):
        run_universe_scan(con, assets, weekend, CFG)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scan_engine.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'compute_scan_metrics'`.

- [ ] **Step 3: Implement**

Append to `src/rs_spy/scan/engine.py` (extend the imports at top with `from dataclasses import dataclass` and `import duckdb`):

```python
class ScanCoverageError(RuntimeError):
    """Refusal to emit a snapshot: too few listing-eligible symbols have a bar
    for as_of (holiday, half-day quirk, or upstream data outage)."""


@dataclass(frozen=True)
class ScanResult:
    as_of: pd.Timestamp
    evaluated: pd.DataFrame
    funnel: dict

    @property
    def passing(self) -> list[str]:
        return sorted(self.evaluated.index[self.evaluated["passed"]])


def compute_scan_metrics(
    con: "duckdb.DuckDBPyConnection", as_of, adv_window: int = 20
) -> pd.DataFrame:
    """Per-symbol as-of metrics from cached daily bars.

    Causality by construction: the WHERE clause admits only bars dated <= as_of
    (daily bars are timestamped at midnight ET = 04:00/05:00 UTC, so CAST(ts AS
    DATE) is the ET session date). The ADV window is the symbol's last
    `adv_window` BARS, not calendar days (see task note).
    """
    as_of_date = pd.Timestamp(as_of).date()
    df = con.execute(
        """
        WITH ranked AS (
            SELECT symbol, ts, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY ts DESC) AS rn
            FROM bars
            WHERE timespan = 'day' AND CAST(ts AS DATE) <= ?
        )
        SELECT symbol,
               max(CASE WHEN rn = 1 THEN close END)            AS last_close,
               max(CASE WHEN rn = 1 THEN CAST(ts AS DATE) END) AS last_bar_date,
               avg(volume)         FILTER (WHERE rn <= ?)      AS adv_shares,
               avg(close * volume) FILTER (WHERE rn <= ?)      AS adv_dollars,
               count(*)            FILTER (WHERE rn <= ?)      AS n_bars
        FROM ranked
        GROUP BY symbol
        """,
        [as_of_date, adv_window, adv_window, adv_window],
    ).df()
    df["last_bar_date"] = pd.to_datetime(df["last_bar_date"])
    df["n_bars"] = df["n_bars"].astype(int)
    return df.set_index("symbol")


def run_universe_scan(
    con: "duckdb.DuckDBPyConnection",
    assets: pd.DataFrame,
    as_of,
    config: ScanConfig | None = None,
) -> ScanResult:
    """The nightly scan and the point-in-time reconstruction -- one code path.

    as_of=today against tonight's refreshed bars is the live scan; as_of=any
    past trading date reconstructs the universe as it would have been (with
    the disclosed survivorship limit: `assets` is always the CURRENT listing).
    """
    config = config or ScanConfig()
    as_of = pd.Timestamp(as_of)
    metrics = compute_scan_metrics(con, as_of, adv_window=config.adv_window)
    evaluated, funnel = apply_gates(assets, metrics, config)

    listing_eligible = evaluated["first_fail"].ne("listing")
    if listing_eligible.any():
        have_asof = float(
            (evaluated.loc[listing_eligible, "last_bar_date"] == as_of.normalize()).mean()
        )
    else:
        have_asof = 0.0
    if have_asof < config.min_coverage_fraction:
        raise ScanCoverageError(
            f"only {have_asof:.0%} of listing-eligible symbols have a bar for "
            f"{as_of.date()} (< {config.min_coverage_fraction:.0%}) -- "
            "holiday, weekend, or data outage?"
        )
    return ScanResult(as_of=as_of, evaluated=evaluated, funnel=funnel)
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_scan_engine.py -v && python -m pytest -q && ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/scan/engine.py tests/unit/test_scan_engine.py
git commit -m "M9: as-of scan metrics + run_universe_scan (no-lookahead tested, coverage refusal)"
```

---

### Task 5: Postgres store — scan tables + repository

**Files:**
- Modify: `src/rs_spy/store/schema.py`, `src/rs_spy/store/__init__.py`, `tests/integration/conftest.py`
- Create: `src/rs_spy/store/scan_repository.py`
- Test: `tests/integration/test_scan_repository.py`

**Interfaces:**
- Produces (all take a psycopg connection; writes commit via transaction, mirroring `repository.py`):
  - `save_scan(conn, scan_date, evaluated: pd.DataFrame, funnel: dict) -> None` — upserts `scan_runs`, replaces that date's `universe_snapshots` rows.
  - `get_universe_snapshot(conn, scan_date, passed_only=False) -> pd.DataFrame`
  - `get_scan_funnel(conn, scan_date) -> dict | None`
  - `save_screener_snapshot(conn, snapshot_date, endpoint: str, payload: dict) -> None` (upsert)
  - `get_screener_snapshot(conn, snapshot_date, endpoint) -> dict | None`
  - `record_onboarded(conn, symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history) -> bool` — `ON CONFLICT DO NOTHING`, returns True only when newly inserted.
  - `list_onboarded(conn) -> pd.DataFrame` (columns `symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history`; empty frame with those columns when none).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_scan_repository.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_scan_repository.py -v`
Expected (with Docker): FAIL — `ModuleNotFoundError: No module named 'rs_spy.store.scan_repository'`. (Without Docker they skip — run these on this machine, which has Docker.)

- [ ] **Step 3: Implement**

In `src/rs_spy/store/schema.py`, append to `_SCHEMA` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS scan_runs (
    scan_date    DATE PRIMARY KEY,
    funnel       JSONB NOT NULL,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    scan_date    DATE NOT NULL,
    symbol       TEXT NOT NULL,
    name         TEXT,
    exchange     TEXT,
    optionable   BOOLEAN,
    last_close   DOUBLE PRECISION,
    adv_shares   DOUBLE PRECISION,
    adv_dollars  DOUBLE PRECISION,
    n_bars       INTEGER,
    passed       BOOLEAN NOT NULL,
    first_fail   TEXT,
    PRIMARY KEY (scan_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_passed
    ON universe_snapshots (scan_date) WHERE passed;

CREATE TABLE IF NOT EXISTS screener_snapshots (
    snapshot_date DATE NOT NULL,
    endpoint      TEXT NOT NULL,
    payload       JSONB NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, endpoint)
);

CREATE TABLE IF NOT EXISTS onboarded_symbols (
    symbol               TEXT PRIMARY KEY,
    onboarded_date       DATE NOT NULL,
    source               TEXT NOT NULL,
    history_start        DATE,
    n_daily_bars         INTEGER,
    insufficient_history BOOLEAN NOT NULL DEFAULT false
);
```

Update the schema module docstring's table list to mention the four scan tables (one line: "M9 adds scan_runs / universe_snapshots / screener_snapshots / onboarded_symbols — the discovery half's records; same idempotent-DDL approach").

Create `src/rs_spy/store/scan_repository.py`:

```python
"""Plain-SQL repository for the scan tables (scan_runs, universe_snapshots,
screener_snapshots, onboarded_symbols). Style mirrors store/repository.py:
raw SQL, callers own the connection, writes commit.
"""
import math

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

_SNAPSHOT_COLS = (
    "symbol", "name", "exchange", "optionable", "last_close",
    "adv_shares", "adv_dollars", "n_bars", "passed", "first_fail",
)
_ONBOARDED_COLS = (
    "symbol", "onboarded_date", "source", "history_start",
    "n_daily_bars", "insufficient_history",
)


def _null_if_nan(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def save_scan(conn: psycopg.Connection, scan_date, evaluated: pd.DataFrame, funnel: dict) -> None:
    """Upsert the funnel row and REPLACE the date's snapshot rows (delete +
    COPY inside one transaction), so a re-run of the same night converges."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_runs (scan_date, funnel) VALUES (%s, %s) "
                "ON CONFLICT (scan_date) DO UPDATE SET funnel=excluded.funnel, captured_at=now()",
                (scan_date, Jsonb(funnel)),
            )
            cur.execute("DELETE FROM universe_snapshots WHERE scan_date=%s", (scan_date,))
            with cur.copy(
                "COPY universe_snapshots (scan_date, symbol, name, exchange, optionable, "
                "last_close, adv_shares, adv_dollars, n_bars, passed, first_fail) FROM STDIN"
            ) as copy:
                for sym, row in evaluated.iterrows():
                    copy.write_row(
                        (
                            scan_date, sym, row["name"], row["exchange"],
                            bool(row["optionable"]),
                            _null_if_nan(row["last_close"]),
                            _null_if_nan(row["adv_shares"]),
                            _null_if_nan(row["adv_dollars"]),
                            int(row["n_bars"]),
                            bool(row["passed"]),
                            _null_if_nan(row["first_fail"]),
                        )
                    )


def get_universe_snapshot(
    conn: psycopg.Connection, scan_date, passed_only: bool = False
) -> pd.DataFrame:
    extra = " AND passed" if passed_only else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_SNAPSHOT_COLS)} FROM universe_snapshots "
            f"WHERE scan_date=%s{extra} ORDER BY symbol",
            (scan_date,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=list(_SNAPSHOT_COLS))


def get_scan_funnel(conn: psycopg.Connection, scan_date) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT funnel FROM scan_runs WHERE scan_date=%s", (scan_date,))
        row = cur.fetchone()
    return row["funnel"] if row else None


def save_screener_snapshot(conn: psycopg.Connection, snapshot_date, endpoint: str, payload: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO screener_snapshots (snapshot_date, endpoint, payload) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (snapshot_date, endpoint) "
            "DO UPDATE SET payload=excluded.payload, captured_at=now()",
            (snapshot_date, endpoint, Jsonb(payload)),
        )
    conn.commit()


def get_screener_snapshot(conn: psycopg.Connection, snapshot_date, endpoint: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM screener_snapshots WHERE snapshot_date=%s AND endpoint=%s",
            (snapshot_date, endpoint),
        )
        row = cur.fetchone()
    return row["payload"] if row else None


def record_onboarded(
    conn: psycopg.Connection,
    symbol: str,
    onboarded_date,
    *,
    source: str,
    history_start,
    n_daily_bars: int,
    insufficient_history: bool,
) -> bool:
    """First insert wins (a repeat most-actives appearance must not re-onboard).
    Returns True only when this call inserted the row."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO onboarded_symbols "
            "(symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) DO NOTHING",
            (symbol, onboarded_date, source, history_start, n_daily_bars, insufficient_history),
        )
        inserted = cur.rowcount == 1
    conn.commit()
    return inserted


def list_onboarded(conn: psycopg.Connection) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_ONBOARDED_COLS)} FROM onboarded_symbols ORDER BY onboarded_date, symbol"
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=list(_ONBOARDED_COLS))
```

In `src/rs_spy/store/__init__.py`, add to the docstring one line ("M9: scan_repository holds the discovery-half records") — do **not** re-export the scan functions; callers import `from rs_spy.store import scan_repository` directly (keeps the `__all__` surface for backtest runs unchanged).

In `tests/integration/conftest.py`, update the truncation in `pg_conn` to cover the new tables:

```python
        cur.execute(
            "TRUNCATE runs, trades, equity_curves, scan_runs, universe_snapshots, "
            "screener_snapshots, onboarded_symbols RESTART IDENTITY CASCADE"
        )
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/integration/test_scan_repository.py -v && python -m pytest -q && ruff check .`
Expected: PASS (integration tests run with Docker up; auto-skip otherwise).

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/store/schema.py src/rs_spy/store/scan_repository.py src/rs_spy/store/__init__.py tests/integration/conftest.py tests/integration/test_scan_repository.py
git commit -m "M9: scan store tables + repository (snapshots, screener, onboarded)"
```

---

### Task 6: Most-active onboarding — candidate selection + per-symbol backfill

**Files:**
- Create: `src/rs_spy/scan/onboarding.py`
- Test: `tests/unit/test_scan_onboarding.py`

**Interfaces:**
- Consumes: Task 1's `most_actives_volume` payload shape (`payload["most_actives"]` list of dicts with `"symbol"`); `rs_spy.data.ingest.backfill`.
- Produces: `MIN_HISTORY_DAYS = 300`; `OnboardingOutcome` (frozen dataclass: `symbol, history_start (date|None), n_daily_bars, n_minute_bars, insufficient_history`); `select_onboarding_candidates(payload, passing: set, curated: set, onboarded: set, top_n=10) -> list[str]`; `onboard_symbol(con, client, symbol, end: datetime, years=5) -> OnboardingOutcome`.
- Contract for Task 8: an outcome with `n_daily_bars == 0 or n_minute_bars == 0` means the backfill did not complete — the caller must NOT record it onboarded (the manifest's error-retry semantics make the next night's attempt resume where this one failed).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_scan_onboarding.py`:

```python
"""Onboarding: gate-filtered candidate selection + per-symbol dual backfill."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scan_onboarding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rs_spy.scan.onboarding'`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/scan/onboarding.py`:

```python
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
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import duckdb

from rs_spy.data.ingest import backfill

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
    Order-preserving (most active first), deduplicated."""
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
    partial failure leaves 'error' manifest units that retry next run."""
    start = end - timedelta(days=365 * years + 5)
    backfill(con, client, [symbol], "day", start, end, chunk_freq="year")
    backfill(con, client, [symbol], "minute", start, end, chunk_freq="month")

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
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_scan_onboarding.py -v && python -m pytest -q && ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/scan/onboarding.py tests/unit/test_scan_onboarding.py
git commit -m "M9: most-active onboarding (candidate selection + dual backfill)"
```

---

### Task 7: `BacktestConfigM5.extra_symbols` — serialize round-trip, runner merge, calendar guard

**Files:**
- Modify: `src/rs_spy/backtest/engine_m5.py` (config field only), `src/rs_spy/store/serialize.py`, `src/rs_spy/jobs/runner.py`
- Test: `tests/unit/test_store_serialize.py` (append), `tests/unit/test_jobs_symbols.py` (create), `tests/unit/test_engine_m5_backtest.py` (append)

**Interfaces:**
- Produces: `BacktestConfigM5.extra_symbols: tuple = ()` — **inert inside the engine** (the engine's symbol set is whatever frames the caller passes); consumed only by `jobs/runner.py`, which merges it into the loaded/traded symbol set. Stored in the run's config JSONB → full provenance of which onboarded symbols each tagged run included.
- Produces: `jobs.runner._trade_symbols(universe, config) -> list[str]` — `universe.trade_symbols` plus any `extra_symbols` not already in `universe.all_symbols`, order-preserving.
- Guard: a dedicated test that a short-history extra symbol cannot shrink `PreparedM5.calendar` (the spec's partial-history guard, enforced by test).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_store_serialize.py`:

```python
def test_extra_symbols_round_trips_as_a_tuple():
    from rs_spy.store.serialize import config_from_jsonb, config_to_jsonb

    cfg = BacktestConfigM5(extra_symbols=("HOOD", "SOFI"))
    data = config_to_jsonb(cfg)
    assert data["extra_symbols"] == ["HOOD", "SOFI"]  # JSON-safe list in storage
    back = config_from_jsonb(data)
    assert back.extra_symbols == ("HOOD", "SOFI")  # tuple again (dataclass eq/hash)
    assert back == cfg
```

(`BacktestConfigM5` is already imported at the top of that test file; if not, add `from rs_spy.backtest.engine_m5 import BacktestConfigM5`.)

Create `tests/unit/test_jobs_symbols.py`:

```python
"""jobs.runner._trade_symbols: curated + extra_symbols merge."""
from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.jobs.runner import _trade_symbols
from rs_spy.universe import BenchmarkSpec, SymbolSpec, Universe

UNIVERSE = Universe(
    benchmarks=[BenchmarkSpec(symbol="SPY", role="primary"),
                BenchmarkSpec(symbol="QQQ", role="secondary")],
    universe=[SymbolSpec(symbol="AAPL", sector="Technology"),
              SymbolSpec(symbol="JPM", sector="Financials")],
)


def test_default_config_reproduces_the_curated_universe_exactly():
    assert _trade_symbols(UNIVERSE, BacktestConfigM5()) == ["AAPL", "JPM"]


def test_extra_symbols_are_appended_and_dupes_of_curated_or_benchmarks_dropped():
    cfg = BacktestConfigM5(extra_symbols=("HOOD", "AAPL", "SPY", "SOFI"))
    assert _trade_symbols(UNIVERSE, cfg) == ["AAPL", "JPM", "HOOD", "SOFI"]
```

Append to `tests/unit/test_engine_m5_backtest.py` (uses the existing `universe` fixture and `_build_m1`/`_build_d1`/`DATES` helpers already in that file):

```python
def test_short_history_extra_symbol_cannot_shrink_the_master_calendar(universe):
    """Spec guard (M9 onboarding): a newly onboarded symbol with short history
    must extend, never truncate, the shared picture. The master calendar is
    SPY's own M5 index, so adding a symbol that only traded the last 3
    sessions must leave the calendar bit-for-bit identical."""
    from rs_spy.data.resample import resample_ohlcv

    short_m1 = _build_m1(DATES[-3:], start_price=30.0, seed=77)
    short_m5 = resample_ohlcv(short_m1, "5min")
    short_d1 = _build_d1(short_m1)

    base = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        earnings_blackout=None,
        config=BacktestConfigM5(),
    )
    with_short = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"], "SHORTY": short_m1},
        universe_m5={"AAPL": universe["aapl_m5"], "SHORTY": short_m5},
        universe_d1={"AAPL": universe["aapl_d1"], "SHORTY": short_d1},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology", "SHORTY": "UNKNOWN"},
        earnings_blackout=None,
        config=BacktestConfigM5(),
    )
    assert with_short.calendar.equals(base.calendar)
```

(Check the `universe` fixture's dict keys before finalizing — it returns keys like `"spy_m1"`/`"aapl_m5"` etc.; if the actual key names differ, use the fixture's real names.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_store_serialize.py tests/unit/test_jobs_symbols.py -v`
Expected: FAIL — `TypeError: BacktestConfigM5.__init__() got an unexpected keyword argument 'extra_symbols'`, and `ImportError: cannot import name '_trade_symbols'`. (The calendar test may pass already — SPY-derived calendar is existing behavior — that's fine; it's a regression guard.)

- [ ] **Step 3: Implement**

In `src/rs_spy/backtest/engine_m5.py`, add as the LAST field of `BacktestConfigM5`:

```python
    # M9 onboarding: additional trade symbols beyond config/universe.yaml.
    # INERT inside this engine (the symbol set is whatever frames the caller
    # passes) -- consumed by jobs/runner.py, which loads + trades these on top
    # of the curated universe. Lives on the config so the runs-store JSONB
    # records exactly which onboarded symbols each tagged run included.
    extra_symbols: tuple = ()
```

In `src/rs_spy/store/serialize.py`:
- `config_to_jsonb`: after the `disabled_gates` line, add:

```python
    d["extra_symbols"] = list(d["extra_symbols"])
```

- `config_from_jsonb`: after the `disabled_gates` coercion, add:

```python
    if "extra_symbols" in kwargs:
        kwargs["extra_symbols"] = tuple(kwargs["extra_symbols"])
```

In `src/rs_spy/jobs/runner.py`, add after `_git_sha`:

```python
def _trade_symbols(universe, config: BacktestConfigM5) -> list[str]:
    """Curated trade symbols plus config.extra_symbols (M9 onboarding),
    order-preserving, minus anything already curated or a benchmark."""
    known = set(universe.all_symbols)
    extra = [s for s in config.extra_symbols if s not in known]
    return [*universe.trade_symbols, *extra]
```

and rewire `_execute_backtest`: replace

```python
        all_m1 = load_universe_m1_bars(con, universe.all_symbols)
        all_m5 = load_universe_m5_bars(con, universe.all_symbols)
        all_d1 = load_universe_daily_bars(con, universe.all_symbols)
```

with

```python
        trade_symbols = _trade_symbols(universe, config)
        load_symbols = list(dict.fromkeys([*universe.all_symbols, *trade_symbols]))
        all_m1 = load_universe_m1_bars(con, load_symbols)
        all_m5 = load_universe_m5_bars(con, load_symbols)
        all_d1 = load_universe_daily_bars(con, load_symbols)
```

and replace

```python
    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    trade_symbols = universe.trade_symbols
    sectors = {s.symbol: s.sector for s in universe.universe}
```

with

```python
    spy, qqq = universe.primary_benchmark, universe.secondary_benchmark
    sectors = {s.symbol: s.sector for s in universe.universe}
    for sym in trade_symbols:
        sectors.setdefault(sym, "UNKNOWN")  # onboarded symbols have no GICS mapping (v1)
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/unit/test_store_serialize.py tests/unit/test_jobs_symbols.py tests/unit/test_engine_m5_backtest.py -v && python -m pytest -q && ruff check .`
Expected: PASS; default-config behavior unchanged (`_trade_symbols` with empty `extra_symbols` returns exactly `universe.trade_symbols`).

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/backtest/engine_m5.py src/rs_spy/store/serialize.py src/rs_spy/jobs/runner.py tests/unit/test_store_serialize.py tests/unit/test_jobs_symbols.py tests/unit/test_engine_m5_backtest.py
git commit -m "M9: BacktestConfigM5.extra_symbols + runner merge + calendar-invariance guard"
```

---

### Task 8: Nightly orchestrator + CLI

**Files:**
- Create: `src/rs_spy/scan/nightly.py`, `scripts/run_nightly_scan.py`
- Test: `tests/integration/test_nightly_scan.py`

**Interfaces:**
- Consumes: everything above — `fetch_assets`/`fetch_screener_snapshots` (T1), `connect_scan`/`refresh_daily_bars` (T2), `run_universe_scan`/`ScanCoverageError` (T3/4), `scan_repository` (T5), `select_onboarding_candidates`/`onboard_symbol` (T6), `extra_symbols`+`create_run`/`launch_run` (T7); `rs_spy.data.warehouse.connect` (main warehouse, read-write, onboarding only).
- Produces: `NightlyReport` dataclass (`scan_date, n_assets, n_passed, scan_saved, screener_saved, onboarded: list[str], launched_run_id: str|None, errors: list[str]`); `run_nightly(settings, client, pg_conn, *, as_of=None, config=None, top_n=10, onboard=True, launch=True) -> NightlyReport`; `scripts/run_nightly_scan.py` typer CLI with `--as-of`, `--feed`, `--top`, `--no-onboard`, `--no-launch`.
- Error isolation contract: screener failure never blocks the scan snapshot; a single symbol's onboarding failure never blocks the others; the launched run includes ALL sufficient-history onboarded symbols (cumulative), not just tonight's.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_nightly_scan.py`:

```python
"""Nightly orchestration against ephemeral Postgres + tmp DuckDB files.

Uses the pg_conn fixture (testcontainers, auto-skip without Docker) and a
FakeClient -- no network, no real warehouse.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from rs_spy.data.alpaca_client import ASSET_COLUMNS, BAR_COLUMNS
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.nightly import run_nightly
from rs_spy.store import scan_repository as scan_repo

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


def test_happy_path_scan_screener_onboard_launch(tmp_path, pg_conn, launched, curated):
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


def test_screener_failure_does_not_block_the_scan(tmp_path, pg_conn, launched, curated):
    client = FakeClient()
    client.fetch_screener_snapshots = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
    report = run_nightly(_settings(tmp_path), client, pg_conn, as_of=AS_OF, config=CFG)
    assert report.scan_saved is True
    assert report.screener_saved is False
    assert any("screener" in e for e in report.errors)
    assert report.onboarded == []  # no payload -> no onboarding, but no crash


def test_second_night_is_idempotent_no_reonboard_no_new_run(tmp_path, pg_conn, launched, curated):
    settings = _settings(tmp_path)
    run_nightly(settings, FakeClient(), pg_conn, as_of=AS_OF, config=CFG)
    report2 = run_nightly(settings, FakeClient(), pg_conn, as_of=AS_OF, config=CFG)
    assert report2.onboarded == []          # HOOD already onboarded -> skipped
    assert len(launched) == 1               # no second run launched
    assert len(scan_repo.list_onboarded(pg_conn)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_nightly_scan.py -v`
Expected (Docker up): FAIL — `ModuleNotFoundError: No module named 'rs_spy.scan.nightly'`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/scan/nightly.py`:

```python
"""Nightly discovery orchestration: refresh -> scan -> record -> onboard -> re-run.

Each stage is isolated: a screener failure never blocks the scan snapshot, one
symbol's failed onboarding never blocks the others, and every failure lands in
NightlyReport.errors instead of killing the job. The scan itself refusing
(ScanCoverageError -- holiday/outage) DOES propagate: no snapshot should exist
for such a night.

Scheduling (documented, not auto-installed). 17:00 ET capture, RTH-only policy
(see the spec). This machine runs America/Chicago, so 16:00 CT == 17:00 ET:

    crontab -e
    0 16 * * 1-5  cd /Users/johnoverton/Development/rs-spy && .venv/bin/python scripts/run_nightly_scan.py >> logs/nightly_scan.log 2>&1
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta, timezone

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
        as_of = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    as_of = pd.Timestamp(as_of)
    scan_date = as_of.date()
    # backfill/refresh end: exclusive upper bound just past the as-of session
    end = (as_of + pd.Timedelta(days=1)).tz_localize(timezone.utc).to_pydatetime()
    report = NightlyReport(scan_date=scan_date)

    # 1) assets + broad daily refresh + scan (a ScanCoverageError propagates:
    #    no snapshot must exist for a holiday/outage night)
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

    # 2) screener capture (isolated)
    snapshots = None
    try:
        snapshots = client.fetch_screener_snapshots()
        for endpoint, payload in snapshots.items():
            scan_repo.save_screener_snapshot(pg_conn, scan_date, endpoint, payload)
        report.screener_saved = True
    except Exception as exc:  # noqa: BLE001 -- isolated stage, recorded not raised
        logger.exception("screener capture failed")
        report.errors.append(f"screener: {exc}")

    # 3) onboarding (isolated per symbol) + tagged re-run
    if onboard and snapshots and snapshots.get("most_actives_volume"):
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
```

Create `scripts/run_nightly_scan.py`:

```python
"""M9 nightly discovery job: refresh broad daily bars, run the universe scan,
record screener snapshots, onboard qualifying most-actives, launch a tagged
backtest over curated + onboarded symbols.

    python scripts/run_nightly_scan.py                    # tonight, iex thresholds
    python scripts/run_nightly_scan.py --as-of 2026-07-01 # re-run/backdate a night
    python scripts/run_nightly_scan.py --no-onboard       # scan + record only

Needs .env (Alpaca keys) and Postgres up (docker compose up -d). Scheduling:
see rs_spy/scan/nightly.py's docstring (cron at 16:00 America/Chicago ==
17:00 ET, weekdays).
"""
import logging

import typer

from rs_spy.config import get_settings
from rs_spy.data.alpaca_client import AlpacaClient
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import ScanCoverageError
from rs_spy.scan.nightly import run_nightly
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema

app = typer.Typer()


@app.command()
def main(
    as_of: str = typer.Option(None, help="Scan date YYYY-MM-DD (default: today ET)"),
    feed: str = typer.Option("iex", help="Threshold preset: iex or sip"),
    top: int = typer.Option(10, help="Most-active candidates to consider for onboarding"),
    no_onboard: bool = typer.Option(False, help="Skip onboarding entirely"),
    no_launch: bool = typer.Option(False, help="Onboard but don't launch the backtest re-run"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    client = AlpacaClient(settings)
    conn = connect_pg()
    try:
        init_schema(conn)
        report = run_nightly(
            settings, client, conn,
            as_of=as_of, config=ScanConfig.for_feed(feed), top_n=top,
            onboard=not no_onboard, launch=not no_launch,
        )
    except ScanCoverageError as exc:
        typer.echo(f"scan refused: {exc}")
        raise typer.Exit(1) from exc
    finally:
        conn.close()

    typer.echo(
        f"{report.scan_date}: {report.n_passed}/{report.n_assets} passed; "
        f"screener={'ok' if report.screener_saved else 'FAILED'}; "
        f"onboarded={report.onboarded or '[]'}; "
        f"run={report.launched_run_id or '-'}"
    )
    for err in report.errors:
        typer.echo(f"  warning: {err}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests, full suite, lint**

Run: `python -m pytest tests/integration/test_nightly_scan.py -v && python -m pytest -q && ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/scan/nightly.py scripts/run_nightly_scan.py tests/integration/test_nightly_scan.py
git commit -m "M9: nightly orchestrator + run_nightly_scan CLI"
```

---

### Task 9: Real-data run, IEX threshold calibration, docs

This task runs against real Alpaca data and needs `.env` + Postgres up. Parts marked **(user)** are long-running and may be run by the user; everything else is verification + documentation.

**Files:**
- Modify (if calibration demands): `src/rs_spy/scan/config.py` (IEX threshold constants, with measured numbers in the comment)
- Modify: `IMPLEMENTATION.md` (new "M9: nightly universe scan" section), `CLAUDE.md` (codebase map + how-to-run rows), `.env.example` (optional `SCAN_WAREHOUSE_PATH` comment line)

- [ ] **Step 1 (user): Initial broad backfill + first scan, onboarding disabled**

Run: `python scripts/run_nightly_scan.py --no-onboard`
Expected: the first run performs the one-time ~5-year broad daily backfill (~10-11k symbols; roughly 300-400 rate-limited requests plus pagination — expect ~15-45 min), then prints `<date>: <n_passed>/<n_assets> passed; screener=ok; ...`. Re-run the same command afterward: it must complete in a few minutes (manifest no-ops + tail refresh only) and converge to the same counts.

- [ ] **Step 2: Calibrate the IEX thresholds**

Sanity target from the spec: passing set lands near **800–1,500**. Inspect the funnel and threshold sensitivity directly against the scan warehouse:

```python
# python - <<'EOF'  (or a notebook)
from rs_spy.config import get_settings
from rs_spy.data.alpaca_client import AlpacaClient
from rs_spy.scan.bars import connect_scan
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import run_universe_scan
import pandas as pd

settings = get_settings()
client = AlpacaClient(settings)
assets = client.fetch_assets()
con = connect_scan(settings.resolved_scan_warehouse_path(), read_only=True)
as_of = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None) - pd.tseries.offsets.BDay(1)
for shares, dollars in [(20_000, 500_000), (30_000, 750_000), (50_000, 1_250_000)]:
    cfg = ScanConfig(min_adv_shares=shares, min_adv_dollars=dollars)
    r = run_universe_scan(con, assets, as_of, cfg)
    print(shares, dollars, r.funnel)
EOF
```

Also cross-check the known 130: the curated universe symbols should overwhelmingly pass (spot-check misses — a curated symbol failing the scan is either a real threshold miscalibration or a real liquidity change worth knowing). If the default (30k/$750k) lands outside 800–1,500, update `IEX_MIN_ADV_SHARES`/`IEX_MIN_ADV_DOLLARS` in `scan/config.py` with the measured pass-counts quoted in the comment, and re-run `python -m pytest -q` (the config tests assert only inequalities, deliberately).

- [ ] **Step 3: Point-in-time spot-check**

Run `run_universe_scan` at three historical dates (e.g. ~1y, ~2y, ~4y back — trading days) via the snippet above with `as_of` overridden. Expected: each returns a plausible-sized passing set (same order of magnitude), no `ScanCoverageError` on real trading days, and a deliberately chosen weekend date DOES raise `ScanCoverageError`. Record the three (date, n_passed) pairs for the docs.

- [ ] **Step 4 (user): First full nightly run with onboarding**

Run: `python scripts/run_nightly_scan.py`
Expected: scan + screener snapshots saved; the top-10 most-actives get gate-filtered (expect most to be filtered: ETFs/sub-$10); any qualifying new symbol backfills (~2-5 min each) and a tagged run `onboarding-<date>` appears in the runs-store (`status=queued->running->succeeded`, visible via `list_runs`). If zero candidates qualify that night, that's a valid outcome — verify the report says `onboarded=[]` and no run was launched.

- [ ] **Step 5: Update docs**

- `IMPLEMENTATION.md`: add an "M9: nightly universe scan (discovery)" section — what was built (mirroring the spec's component list), the calibration numbers measured in Step 2, the PIT spot-check results from Step 3, the first real nightly-run outcome from Step 4, and the disclosed limits (survivorship, ETF heuristics, float substitution, dropped halt gate). Update the milestone tracker list at the top.
- `CLAUDE.md`: add `scan/` to the codebase map, `scripts/run_nightly_scan.py` to the how-to-run table, and a Data & storage bullet for `data/scan.duckdb` + the four new Postgres tables.
- `.env.example`: add `# SCAN_WAREHOUSE_PATH=  # optional; defaults to data/scan.duckdb`.

- [ ] **Step 6: Final verification + commit**

Run: `python -m pytest -q && ruff check .`
Expected: green + clean.

```bash
git add IMPLEMENTATION.md CLAUDE.md .env.example src/rs_spy/scan/config.py
git commit -m "M9: real-data calibration + docs (IMPLEMENTATION.md, CLAUDE.md)"
```

---

## Self-review notes (spec coverage)

- Separate daily-bars storage → Task 2 (separate DuckDB file — stronger isolation than a separate table, same intent; documented in config.py comment).
- One code path live/PIT + no-lookahead test → Task 4.
- Gate mapping incl. disclosed substitutions → Tasks 3 (config docstring) + `scan/__init__.py` (Task 2).
- Funnel + partition test → Tasks 3/4.
- Coverage refusal → Task 4; holiday behavior exercised in Task 4's weekend test and Task 9 Step 3.
- Store tables (scan_runs, universe_snapshots, screener_snapshots, onboarded_symbols) + upsert idempotency → Task 5.
- Screener recorder + real-time-only rationale → Tasks 1, 8.
- Onboarding: gate-filter, dual backfill, first-insert-wins idempotency, incomplete-backfill retry, insufficient-history exclusion from runs, cumulative tagged re-run via `extra_symbols` → Tasks 6, 7, 8.
- Partial-history guard "verified by a dedicated test, not assumed" → Task 7's calendar-invariance test.
- 17:00 ET / RTH-only / cron documentation → Task 8 (`nightly.py` docstring), loader `rth_only=True` convention untouched.
- Feed switch → Task 3 presets + `--feed` CLI flag (Task 8).
- IEX threshold calibration open question → Task 9 Step 2.
- Out-of-scope confirmations: no change to `universe.yaml`, no bulk minute backfill, no live signal engine — no task touches them.
