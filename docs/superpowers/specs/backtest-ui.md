# Spec: Backtest UI (M8)

**Status**: design spec (not built). Input to a future `writing-plans` → implementation milestone.
**Depends on**: the Postgres runs-store and detached job runner (`src/rs_spy/store/`, `src/rs_spy/jobs/`)
and the read-only DuckDB warehouse — all built alongside this spec.

## Purpose

Give a single-user, local GUI to **tweak M5 backtest settings, fire off runs, and view/compare results** —
replacing the current loop of editing dataclass defaults / passing `--config-json` and opening CSVs by hand.
The heavy lifting (`_prepare_m5`, ~15–20 min) is genuinely slow; the UI's job is to make configuration,
launching, and result inspection pleasant and concurrent — **not** to make a run instant.

## Platform

**Streamlit** (`streamlit run app.py`). Rationale: Python-only (no separate frontend/backend or JS stack this
repo doesn't have), native sliders/inputs and direct rendering of pandas DataFrames + charts, local single-user,
no auth. Add `streamlit` to the `dev`/app extras in `pyproject.toml` when built.

## Job execution model (decided)

A run must survive the browser tab closing and a Streamlit server restart, and multiple runs must execute at
once. Therefore **out-of-process, not an in-Streamlit thread**:

1. User adjusts knobs and clicks **Run**.
2. UI calls `store.create_run(conn, config, status='queued')` → a `runs` row exists immediately (shows as *queued*).
3. UI calls `jobs.launch.launch_run(run_id)` → a **detached subprocess** (`scripts/run_backtest_job.py --run-id`,
   `start_new_session=True`) that opens the warehouse **read-only**, runs the backtest, and writes
   status/metrics/trades/equity to Postgres. Returns instantly.
4. UI **polls Postgres** (`get_run` / `list_runs`) on a refresh interval and renders status + results as they land.

Because each job is its own process (own read-only DuckDB con + own Postgres connection), **runs execute in
parallel**; the run list shows all of them with live status. The only real ceiling is machine memory (each run
loads full bars + `_prepare_m5`) — an operator concern, not a design one. This is exactly the concurrency the
read-only-DuckDB change unlocked.

*Rejected*: an in-process background thread — a 15–20 min run would die with the Streamlit server and be
single-run-at-a-time. *Rejected*: FastAPI + JS frontend + worker queue — far larger build, no upside for a
local single user.

## MVP scope (v1)

**M5 single-run only.** Screens:

1. **Configure & Run** — a form over `BacktestConfigM5` (`src/rs_spy/backtest/engine_m5.py:45`): the gate
   thresholds, RRS window/threshold(s), risk sizing, concurrency caps, disabled-gate toggles, shorts on/off,
   `stop_atr_mult`, dip-hold mode, etc. Prefill from the dataclass defaults; optional label field; **Run** button.
   Optionally seed the form from an existing run's config (clone-and-tweak) via `store.get_config`.
2. **Runs list** — `list_runs()` newest-first: label, status badge, created/finished time, headline metrics
   (n_trades, profit_factor, total_pnl). Auto-refresh; clicking a run opens its detail.
3. **Run detail** — the trade log (`get_trades` → DataFrame), the **equity curve** chart (`get_equity` → line
   chart), the metrics table, and the entry funnel (from `runs.funnel`). Show the exact config used and the
   `error` text if failed.
4. **Compare** — select 2+ completed runs and show their metrics side by side (and optionally overlaid equity
   curves). Fed entirely from the `runs` table.

**Explicitly out of scope for v1** (good future expansions, flagged now): the D1 engine, and the M7 validation
study suite. Both are natural once the core loop proves out.

## Notes / future levers

- **Precompute reality**: surface run status/progress honestly; a fresh run is 15–20 min. Don't imply immediacy.
- **Fast sweeps (future)**: `run_m5_backtest` accepts a pre-built `PreparedM5`, and config fields split into
  **prepare-baked** vs **event-loop-only** (docstring at `engine_m5.py:350-364`). A later UI feature could build
  one `PreparedM5` and cheaply sweep event-loop-only knobs (risk sizing, `stop_atr_mult`, `bias_hold_bars`,
  `dip_hold_mode`, concurrency, shorts) — many runs for the price of one precompute. v1 does not do this.
- **Stale-run handling**: a hard crash can leave a run stuck in `running`; the list view can flag likely-dead
  runs with the reaper query documented in `jobs/launch.py` (`status='running' AND started_at < now() - interval
  '2 hours'`).
- **Legacy CSV outputs** (`reports/m5_backtest/…`) remain untouched; the UI reads exclusively from Postgres.

## Open questions for the implementation plan

- Streamlit refresh mechanism for polling (st_autorefresh vs. manual rerun button) and a sensible interval.
- Whether the run list should page (`list_runs` already supports `limit`/`offset`) or just cap at N.
- Charting: Streamlit's built-in `st.line_chart` vs. Altair for the equity curve / comparison overlays.

## Addendum (2026-07-05, pre-plan): decisions + M9/M10 additions

Written after M9 (nightly universe scan) landed and M10 (universe 500 + campaign) was specced;
this addendum resolves the open questions above and extends v1 scope to the data that now exists.

**Open questions resolved:**

- **Refresh**: `st.fragment(run_every="5s")` around the runs-list/status region (built-in,
  no extra dependency, scopes the rerun to the fragment). Detail pages refresh on navigation;
  no global autorefresh.
- **Run list paging**: newest-first, `limit=50` with a "show more" offset button (reuses
  `list_runs(limit, offset)`); no full pagination UI in v1.
- **Charting**: `st.line_chart` everywhere a single wide DataFrame suffices (equity curve,
  overlaid compare curves); no Altair in v1.

**Structure**: `streamlit run app.py` at repo root; `app.py` is a thin `st.navigation` shell
over page functions living in `src/rs_spy/ui/` (pure data helpers separated from `st.*`
rendering so the data layer is unit-testable; pages exercised hermetically with
`streamlit.testing.v1.AppTest` against a stubbed store). `streamlit` goes in a `ui` extras
group in `pyproject.toml`.

**Scope additions (new since the original spec):**

1. **Scan & discovery page** — reads the M9 Postgres tables: latest `scan_runs` funnel
   (metric cards per gate), passing-count history over scan dates (line chart),
   `universe_snapshots` browser for a chosen date (filterable DataFrame: passed / first_fail),
   and the `onboarded_symbols` table (with `insufficient_history` badges).
2. **Campaign view (M10-aware)** — the runs list groups rows whose labels match the M10
   campaign convention (`m10-<tag>-<variant>-c<n>`); a campaign detail view shows per-cohort
   status and, once all cohorts finish, the aggregated metrics table via
   `backtest/aggregate.py`. (Plain single runs render exactly as in the original spec.)
3. **Config form fields** — the form covers the current `BacktestConfigM5` including the
   M9/M10 additions (`extra_symbols`, `universe_file`, `trade_symbols_override`) as advanced
   fields, defaulted and collapsed.

**Explicitly still out of scope for v1**: real-time signals (that is discovery milestone #2 —
its own spec/brainstorm; the UI will grow a signals page in that milestone, not this one),
the D1 engine, and triggering the M7 study suite from the UI.
