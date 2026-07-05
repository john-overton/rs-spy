# Spec: Nightly Universe Scan (discovery milestone #1)

**Status**: design spec (approved in brainstorming, not built). Input to a `writing-plans` →
implementation milestone.
**Depends on**: the DuckDB warehouse + ingestion stack (`src/rs_spy/data/`), the Postgres
runs-store (`src/rs_spy/store/`), and the detached-job pattern (`src/rs_spy/jobs/`).

## Purpose and context

The system today trades a hand-curated 130-symbol `config/universe.yaml`. The spec
(`algo-spec/01` §4) calls for a **nightly rebuild of an ~800–1,500 symbol liquid-US-equity
universe** — the "what to trade" discovery half. M7.5's closing analysis flagged universe
expansion as the remaining sample-size multiplier (the promoted baseline produces 13 trades /
5 years on 130 symbols; the joint gate-confluence rate makes candidate count the binding
constraint on trade frequency).

This milestone is **live-first**: it builds the real nightly scan and starts recording
discovery data every trading day. It is also the first of three milestones toward a full
paper-trading loop:

1. **Nightly universe scan** (this spec).
2. Live signal engine — live bar ingestion + the existing bias/selection engines at M5
   cadence, emitting a ranked watchlist.
3. Paper execution — orders/stops/exits/risk against Alpaca's paper API.

Alpaca's screener API (`most-actives`, `movers`) is **real-time only** — no as-of/date
parameters exist, so historical screener signal cannot be backfilled. Two consequences drive
this design: (a) the scan itself is *self-computed* from daily bars so it works both live and
point-in-time; (b) a snapshot recorder starts capturing the live screener endpoints now,
because every unrecorded day is lost forever.

## Decisions already made (brainstorming outcomes)

- **Approach**: self-computed scan from Alpaca daily bars (Approach A) + screener-endpoint
  recorder (Approach C) folded into the same nightly job. A second reference-data vendor
  (float, earnings, security type) is explicitly **v2**, not blocking.
- **Data plan**: free IEX tier now; design carries a `feed` (`iex` | `sip`) config switch so a
  later Algo Trader Plus upgrade (~$99/mo, consolidated SIP volume) is a config change plus a
  threshold change, not a code change.
- **Delayed/free data is acceptable for paper trading** as long as no lookahead bias is
  introduced — the engine only ever consumes closed bars (existing codebase discipline).
- **One code path for live and historical**: `run_universe_scan(as_of=...)` reads only the
  warehouse, so `as_of=today` is the nightly scan and `as_of=<past date>` is the backtest
  reconstruction. No divergence between what the backtest assumes and what the live scan does.

## Architecture

New package `src/rs_spy/scan/`, plus small extensions to existing modules.

### Components

- **`data/alpaca_client.py` extensions**
  - `fetch_assets()` — trading API `GET /v2/assets` (via alpaca-py `TradingClient`), returning
    the ~10–11k active `us_equity` assets with `symbol`, `name`, `exchange`, `status`,
    `tradable`, `shortable`, `easy_to_borrow`, `fractionable`, `attributes` (which carries
    `has_options` / `options_enabled`).
  - `fetch_screener_snapshots()` — data API most-actives (by volume and by trades, top 100)
    and market movers (top 50 gainers/losers), returned as raw JSON per endpoint.
- **`scan/config.py`** — `ScanConfig`: `feed` (`iex` | `sip`), per-feed thresholds
  (`min_adv_shares`, `min_adv_dollars`), `min_price` (10.0), `adv_window` (20), exchange
  allowlist, name-pattern blocklist, `min_coverage_fraction` (sanity floor, default 0.80).
  Per-feed defaults: `sip` uses the spec's real values (1,000,000 shares / $25M); `iex` uses
  recalibrated proxies consistent with the existing `min_adv_shares=50_000` precedent
  (IEX volume ≈ 2–3% of consolidated; exact IEX defaults to be calibrated during
  implementation against cached data and documented inline).
- **`scan/engine.py`** — `run_universe_scan(as_of, con, assets, config) -> ScanResult`: a pure
  function over cached daily bars + asset metadata. Returns per-symbol gate outcomes/metrics,
  the passing set, and a per-gate funnel count (the M7.5 funnel pattern applied to discovery,
  so "why did the universe shrink/grow" is always answerable).
- **`store/` extension** — two tables:
  - `universe_snapshots(scan_date, symbol, close, adv_shares, adv_dollars, optionable,
    exchange, passed, gate_fail_reasons, ...)` — one row per evaluated symbol per scan date.
  - `screener_snapshots(snapshot_date, endpoint, payload jsonb, captured_at)`.
  Both upsert-idempotent on their natural keys. A flat artifact (parquet of the passing set
  per scan date, under `reports/universe_scan/`) is written alongside for grep/notebook use.
- **`scripts/run_nightly_scan.py`** — the Typer orchestrator: refresh assets → incremental
  daily-bar backfill for all active symbols → `run_universe_scan(as_of=today)` → write
  snapshots → capture screener endpoints. Safe to run detached (same conventions as
  `jobs/`); scheduling via launchd/cron is documented in the script docstring, not
  auto-installed.

### Gate mapping (algo-spec 01 §4 → implementation)

| # | Spec gate | Implementation | Fidelity |
|---|-----------|----------------|----------|
| 1 | Primary US listing, common stock/ADR, no ETFs/warrants/units | `status=active`, `tradable`, `class=us_equity`, exchange allowlist (NYSE, NASDAQ, AMEX — excluding ARCA/BATS removes most ETFs), name-pattern blocklist ("ETF", "Fund", "Trust", warrant/unit/right suffixes) | **Approximate, disclosed** — Alpaca has no security-type field |
| 2 | Last close ≥ $10 | Direct from cached daily bars | Exact |
| 3 | 20-day ADV ≥ 1M shares AND 20-day avg dollar volume ≥ $25M | Computed from cached daily bars; thresholds from `ScanConfig` per feed (spec values under `sip`, recalibrated proxies under `iex`) | Exact once SIP; calibrated proxy on IEX |
| 4 | Shares float ≥ 50M | **Substituted**: the dollar-volume floor serves as the low-float-gapper proxy | Disclosed gap; v2 vendor enrichment |
| 5 | Not halted in prior 5 sessions; not in bankruptcy/delisting | **Dropped** — no historical halt feed available | Disclosed gap |
| 6 | Optionable (preferred, not required in v1) | `has_options` asset attribute — recorded per symbol, **not gating** | Exact |

## Data flow & timing

- Nightly job at ~17:00 ET on trading days. After close, Alpaca's screener endpoints still
  show that day's actives/movers (they reset at the next open), so one job captures both the
  scan and the screener snapshots.
- **One-time initial backfill**: 5 years of daily bars for the full active asset list
  (~13–14M rows — smaller than the existing 44M-row minute warehouse), through the existing
  `ingest.py`/manifest machinery (batched multi-symbol requests, well within the 200 req/min
  free-tier limit). This makes point-in-time reconstruction available immediately, not after
  months of accumulation.
- **Steady state**: each night fetches only the newly closed day per symbol (manifest-driven),
  plus daily bars for any newly listed assets.
- Universe snapshots accumulate one row-set per trading day; the passing set is expected to
  land in the spec's ~800–1,500 range, but the gates decide — the funnel counts make drift
  visible rather than capping it.

## Point-in-time reconstruction & its limits

`run_universe_scan(as_of=<past date>)` recomputes the universe exactly as the nightly job
would have seen it, using only bars ≤ `as_of`. Known, accepted, **disclosed** limits:

- **Survivorship bias**: the asset list is current-only (Alpaca has no historical listings),
  so reconstructions exclude symbols delisted before today. This inflates historical results
  somewhat (delistings correlate with the losing tail). Documented in the module docstring;
  same "document, don't silently approximate" norm as the IPG lesson.
- **Screener snapshots cannot be reconstructed** — the recorder only accumulates forward.
- Gate-1 heuristics use *current* asset metadata (name/exchange), not as-of metadata.

Wiring the reconstructed universe into `run_m5_backtest` (and the minute-bar backfill for
passing symbols that this implies) is a **follow-up milestone**, deliberately out of scope
here.

## Error handling

- Backfill is manifest-resumable: a killed job re-runs cleanly with no duplicate fetches.
- Per-symbol fetch failures are logged and skipped, never fatal to the job.
- The scan **refuses to emit a snapshot** when fewer than `min_coverage_fraction` of active
  assets have a bar for `as_of` (catches holidays, half-day quirks, and data outages instead
  of silently writing a near-empty universe).
- Screener capture failures do not block the scan (and vice versa); each part reports its own
  status.
- Snapshot writes are upserts — re-running a night is safe and convergent.

## Testing

Consistent with the repo's established norms (hermetic, no network/creds):

- **Golden unit tests** per gate (hand-built asset/bar fixtures exercising pass/fail edges,
  including the $10 boundary, ADV window edges, allowlist/blocklist behavior).
- **No-lookahead test** for `run_universe_scan`: the result at `as_of=t` is unchanged when
  bars after `t` are added to the warehouse — the same causality-test pattern used for every
  indicator.
- **Funnel-partition test**: every evaluated symbol appears in exactly one terminal bucket
  (passed or exactly-attributed fail reasons); counts sum to the asset total.
- **Orchestrator tests** with a mocked client: kill/resume idempotency, per-symbol failure
  isolation, coverage-floor refusal.
- **Postgres round-trip tests** via testcontainers (auto-skip without Docker), matching
  `test_store_repository.py`'s pattern.

## Out of scope (explicit)

- Live intraday signal engine and paper execution (milestones #2 and #3).
- Minute-bar backfill for scanned symbols; backtest integration of the expanded universe.
- Second-vendor reference data: shares float, earnings calendar (would populate the G8 stub),
  security type, halt history — all v2 enrichments of this scan.
- Any change to the existing 130-symbol `config/universe.yaml` or current backtest behavior.

## Open questions for the implementation plan

- Whether broad daily bars share the existing `bars` table (with manifest scoping) or get
  their own table to keep the curated-universe queries unchanged.
- Exact IEX-recalibrated ADV/dollar-volume defaults (calibrate against cached data for the
  130 knowns, then sanity-check the resulting universe size lands near the spec's range).
- Screener capture time within the job (immediately at 17:00 ET vs. a pre-close snapshot
  later when milestone #2 exists).
