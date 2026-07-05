# M8: Backtest UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the spec (`docs/superpowers/specs/backtest-ui.md`, including its 2026-07-05 addendum): a local single-user Streamlit app to configure + launch M5 backtest runs, watch their status, inspect/compare results, and browse the M9 scan/onboarding data — all over the existing Postgres runs-store and detached job runner.

**Architecture:** `streamlit run app.py` at repo root; `app.py` is a thin `st.navigation` shell over page functions in `src/rs_spy/ui/pages.py`. All Postgres access goes through `src/rs_spy/ui/data.py` (thin wrappers over `store/repository.py` + `store/scan_repository.py`), and pages always call them as `data.<fn>(...)` module attributes — that is what makes every page hermetically testable with `streamlit.testing.v1.AppTest` + monkeypatch, no Postgres needed. The config form is generated from `dataclasses.fields(BacktestConfigM5)` by type dispatch (robust to future config fields), with pure, unit-tested coercion helpers in `src/rs_spy/ui/form.py`.

**Tech Stack:** Streamlit ≥1.40 (new `ui` extras), pandas, psycopg3 (via existing store modules), pytest + `streamlit.testing.v1.AppTest`.

## Global Constraints

- Job model (spec, decided): **out-of-process** — Run = `repo.create_run(status='queued')` then `jobs.launch.launch_run(run_id)`; the UI only ever **polls Postgres**. Never run a backtest in a Streamlit thread.
- Refresh (addendum): `st.fragment(run_every="5s")` around the runs-list/status region only; no global autorefresh.
- Runs list (addendum): newest-first, `limit=50` + a "Show more" offset button.
- Charts (addendum): `st.line_chart` only; no Altair in v1.
- Pages always access data as `data.<fn>(...)` (module attribute), never `from ...data import fn` — required for AppTest monkeypatching.
- v1 scope (spec + addendum): Runs list, Run detail, Configure & Run (incl. clone-and-tweak), Compare, Scan & discovery, Campaign view. **Out of scope**: real-time signals (discovery milestone #2), the D1 engine, triggering the M7 study suite.
- `streamlit` lives in a `ui` extras group; importing `rs_spy.ui.form`'s coercion helpers must not require streamlit (pure module).
- Zero change to existing store/jobs/backtest modules (read-only consumers). Existing tests stay green. `ruff check .` (line-length 100) clean before every commit. Run from repo root with `source .venv/bin/activate`; `pip install -e ".[ui]"` once in Task 1.
- **Cross-plan dependency**: Task 7 (Campaign view) imports `rs_spy.backtest.aggregate` from the M10 plan (Tasks 1–6 of `2026-07-05-m10-universe-500.md`). If executing M8 first, do Tasks 1–6 + 8 and return to Task 7 after M10 lands.

## File structure

```
app.py                          CREATE  st.navigation shell (repo root, per spec)
src/rs_spy/ui/__init__.py       CREATE  package docstring
src/rs_spy/ui/data.py           CREATE  get_conn() + PG data helpers (wraps store modules)
src/rs_spy/ui/form.py           CREATE  pure config-form helpers (no streamlit import)
src/rs_spy/ui/pages.py          CREATE  page functions: runs, run_detail, configure, compare, scan, campaigns
pyproject.toml                  MODIFY  [project.optional-dependencies] ui = ["streamlit>=1.40"]
tests/unit/test_ui_form.py      CREATE  pure coercion/grouping tests
tests/unit/test_ui_data.py      CREATE  label parsing + pure helpers
tests/unit/test_ui_pages.py     CREATE  AppTest page tests (stubbed data layer)
```

---

### Task 1: Scaffold — `ui` package, data layer, app shell

**Files:**
- Create: `app.py`, `src/rs_spy/ui/__init__.py`, `src/rs_spy/ui/data.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_ui_data.py`, `tests/unit/test_ui_pages.py` (smoke)

**Interfaces:**
- Consumes: `store/repository.py` (`list_runs, get_run, get_trades, get_equity, get_config, create_run`), `store/scan_repository.py` (`get_universe_snapshot, get_scan_funnel, list_onboarded`), `store/connection.connect_pg`, `store/schema.init_schema`, `jobs/launch.launch_run`, `config.get_settings`.
- Produces (`rs_spy/ui/data.py`): `get_conn()` (st.cache_resource'd `connect_pg` + `init_schema`); `runs_df(conn, limit=50, offset=0) -> pd.DataFrame` (columns `run_id,label,status,created_at,finished_at,n_trades,profit_factor,total_pnl` — headline metrics pulled out of the `metrics` JSONB, None-safe); `run_detail(conn, run_id) -> dict|None`; `trades_df(conn, run_id) -> pd.DataFrame`; `equity_series(conn, run_id) -> pd.Series|None`; `config_of(conn, run_id) -> BacktestConfigM5`; `create_and_launch(conn, config, label) -> uuid.UUID`; `parse_campaign_label(label) -> tuple[str,str,int]|None` (`m10-<tag>-<variant>-c<n>`; tag may contain '-'); `scan_dates(conn) -> list`; `passing_history(conn) -> pd.DataFrame(scan_date, n_passed)`; `scan_funnel(conn, scan_date) -> dict|None`; `universe_snapshot(conn, scan_date) -> pd.DataFrame`; `onboarded_df(conn) -> pd.DataFrame`.
- Produces (`app.py`): `st.navigation` over `pages.runs_page, pages.configure_page, pages.compare_page, pages.scan_page, pages.campaigns_page` (run-detail is reached from the runs page via `st.session_state["selected_run_id"]`, not a nav entry).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ui_data.py`:

```python
"""Pure ui.data helpers (no Postgres, no streamlit widgets exercised)."""
import pandas as pd
import pytest

pytest.importorskip("streamlit")  # rs_spy.ui.data imports streamlit (cache_resource)
from rs_spy.ui.data import parse_campaign_label, _headline_row  # noqa: E402


def test_parse_campaign_label_handles_tags_with_dashes():
    assert parse_campaign_label("m10-jul-05-baseline-c2") == ("jul-05", "baseline", 2)
    assert parse_campaign_label("m10-x-w12-c10") == ("x", "w12", 10)


def test_parse_campaign_label_rejects_non_campaign_labels():
    assert parse_campaign_label(None) is None
    assert parse_campaign_label("onboarding-2026-07-06") is None
    assert parse_campaign_label("m10-missing-cohort") is None


def test_headline_row_is_none_safe_for_queued_runs():
    run = {"run_id": "x", "label": "L", "status": "queued",
           "created_at": pd.Timestamp("2026-07-05"), "finished_at": None,
           "metrics": None}
    row = _headline_row(run)
    assert row["n_trades"] is None and row["profit_factor"] is None
    assert row["status"] == "queued"


def test_headline_row_extracts_metrics_when_present():
    run = {"run_id": "x", "label": "L", "status": "succeeded",
           "created_at": pd.Timestamp("2026-07-05"), "finished_at": pd.Timestamp("2026-07-05"),
           "metrics": {"n_trades": 13, "profit_factor": 3.71, "total_pnl": 753.0}}
    row = _headline_row(run)
    assert row["n_trades"] == 13 and row["profit_factor"] == 3.71 and row["total_pnl"] == 753.0
```

Create `tests/unit/test_ui_pages.py` (smoke only in this task; later tasks extend it):

```python
"""Hermetic page tests: streamlit AppTest + monkeypatched rs_spy.ui.data."""
import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import rs_spy.ui.data as data  # noqa: E402


EMPTY_RUNS = pd.DataFrame(
    columns=["run_id", "label", "status", "created_at", "finished_at",
             "n_trades", "profit_factor", "total_pnl"]
)


def _stub_common(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=50, offset=0: EMPTY_RUNS)


def test_runs_page_renders_empty_store(monkeypatch):
    _stub_common(monkeypatch)
    at = AppTest.from_function(_run_runs_page)
    at.run()
    assert not at.exception


def _run_runs_page():
    from rs_spy.ui.pages import runs_page
    runs_page()
```

(`AppTest.from_function` executes the function as a fresh script in-process, so
monkeypatched `rs_spy.ui.data` attributes are visible — this is why pages must call
`data.fn(...)`. `pytest.importorskip("streamlit")` keeps the suite green for anyone
without the `ui` extras installed.)

- [ ] **Step 2: Install extras, run tests to verify they fail**

Run: `pip install -e ".[ui]"` after adding to `pyproject.toml` extras:

```toml
ui = [
    "streamlit>=1.40",
]
```

Run: `python -m pytest tests/unit/test_ui_data.py tests/unit/test_ui_pages.py -q`
Expected: FAIL with `ModuleNotFoundError: rs_spy.ui`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/ui/__init__.py`:

```python
"""M8 backtest UI (Streamlit, local single-user).

Design: docs/superpowers/specs/backtest-ui.md (+ 2026-07-05 addendum).
Pages render; data.py talks to Postgres; jobs run out-of-process via
jobs/launch. The UI never executes a backtest in-process.
"""
```

Create `src/rs_spy/ui/data.py`:

```python
"""Postgres data layer for the UI. Thin wrappers over store/* so pages stay
render-only. Pages must call these as `data.fn(...)` module attributes --
tests monkeypatch this module and never need Postgres."""
import re
import uuid

import pandas as pd
import streamlit as st

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.config import get_settings
from rs_spy.jobs.launch import launch_run
from rs_spy.store import repository as repo
from rs_spy.store import scan_repository as scan_repo
from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema

_RUN_COLS = ["run_id", "label", "status", "created_at", "finished_at",
             "n_trades", "profit_factor", "total_pnl"]
_CAMPAIGN_RE = re.compile(r"^m10-(.+)-([A-Za-z0-9_]+)-c(\d+)$")


@st.cache_resource
def get_conn():
    conn = connect_pg(get_settings().database_url)
    init_schema(conn)
    return conn


def _headline_row(run: dict) -> dict:
    m = run.get("metrics") or {}
    return {
        "run_id": run["run_id"], "label": run["label"], "status": run["status"],
        "created_at": run["created_at"], "finished_at": run["finished_at"],
        "n_trades": m.get("n_trades"), "profit_factor": m.get("profit_factor"),
        "total_pnl": m.get("total_pnl"),
    }


def runs_df(conn, limit: int = 50, offset: int = 0) -> pd.DataFrame:
    rows = repo.list_runs(conn, limit=limit, offset=offset)
    return pd.DataFrame([_headline_row(r) for r in rows], columns=_RUN_COLS)


def run_detail(conn, run_id) -> dict | None:
    return repo.get_run(conn, uuid.UUID(str(run_id)))


def trades_df(conn, run_id) -> pd.DataFrame:
    return repo.get_trades(conn, uuid.UUID(str(run_id)))


def equity_series(conn, run_id) -> pd.Series | None:
    return repo.get_equity(conn, uuid.UUID(str(run_id)))


def config_of(conn, run_id) -> BacktestConfigM5:
    return repo.get_config(conn, uuid.UUID(str(run_id)))


def create_and_launch(conn, config: BacktestConfigM5, label: str | None) -> uuid.UUID:
    run_id = repo.create_run(conn, config, label=label or None)
    launch_run(run_id)
    return run_id


def parse_campaign_label(label) -> tuple[str, str, int] | None:
    if not label:
        return None
    m = _CAMPAIGN_RE.match(label)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def scan_dates(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT scan_date FROM scan_runs ORDER BY scan_date DESC")
        return [r["scan_date"] for r in cur.fetchall()]


def passing_history(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scan_date, (funnel->>'passed')::int AS n_passed "
            "FROM scan_runs ORDER BY scan_date"
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["scan_date", "n_passed"])


def scan_funnel(conn, scan_date) -> dict | None:
    return scan_repo.get_scan_funnel(conn, scan_date)


def universe_snapshot(conn, scan_date) -> pd.DataFrame:
    return scan_repo.get_universe_snapshot(conn, scan_date)


def onboarded_df(conn) -> pd.DataFrame:
    return scan_repo.list_onboarded(conn)
```

Create `src/rs_spy/ui/pages.py` (this task: `runs_page` placeholder body only; later
tasks replace/extend the specific pages — each later task's step shows the full function):

```python
"""Page render functions. Every data access goes through `data.<fn>(...)`."""
import streamlit as st

import rs_spy.ui.data as data


def runs_page() -> None:
    st.title("Runs")
    conn = data.get_conn()
    df = data.runs_df(conn)
    if df.empty:
        st.info("No runs yet — use Configure & Run.")
        return
    st.dataframe(df, hide_index=True)


def configure_page() -> None:
    st.title("Configure & Run")
    st.info("Coming in Task 4.")


def compare_page() -> None:
    st.title("Compare")
    st.info("Coming in Task 5.")


def scan_page() -> None:
    st.title("Scan & discovery")
    st.info("Coming in Task 6.")


def campaigns_page() -> None:
    st.title("Campaigns")
    st.info("Coming in Task 7.")
```

Create `app.py` (repo root):

```python
"""M8 backtest UI entry point:  streamlit run app.py"""
import streamlit as st

from rs_spy.ui import pages

st.set_page_config(page_title="rs-spy", layout="wide")
nav = st.navigation([
    st.Page(pages.runs_page, title="Runs", url_path="runs", default=True),
    st.Page(pages.configure_page, title="Configure & Run", url_path="run"),
    st.Page(pages.compare_page, title="Compare", url_path="compare"),
    st.Page(pages.scan_page, title="Scan & discovery", url_path="scan"),
    st.Page(pages.campaigns_page, title="Campaigns", url_path="campaigns"),
])
nav.run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_data.py tests/unit/test_ui_pages.py -q`
Expected: all pass. Then full suite + `ruff check .` — green + clean.
Manual smoke (optional, needs Postgres up): `streamlit run app.py --server.headless true` then Ctrl-C.

- [ ] **Step 5: Commit**

```bash
git add app.py src/rs_spy/ui/ pyproject.toml tests/unit/test_ui_data.py tests/unit/test_ui_pages.py
git commit -m "M8: UI scaffold (nav shell, data layer, ui extras)"
```

---

### Task 2: Runs list page (live status fragment, show-more, row selection)

**Files:**
- Modify: `src/rs_spy/ui/pages.py` (replace `runs_page`)
- Test: `tests/unit/test_ui_pages.py` (extend)

**Interfaces:**
- Consumes: Task 1's `data.runs_df`, `data.get_conn`.
- Produces: `runs_page()` — an `@st.fragment(run_every="5s")` region rendering the runs table
  with status badges; a "Show more" button growing `st.session_state["runs_limit"]` by 50;
  row selection (`st.dataframe(..., selection_mode="single-row", on_select="rerun")`) stores
  `st.session_state["selected_run_id"]` and renders the detail inline below (detail body is
  Task 3's `render_run_detail`; this task stubs it with the run's raw dict).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_pages.py`:

```python
def _runs_fixture():
    return pd.DataFrame([
        {"run_id": "11111111-1111-1111-1111-111111111111", "label": "baseline",
         "status": "succeeded", "created_at": pd.Timestamp("2026-07-05 10:00"),
         "finished_at": pd.Timestamp("2026-07-05 10:20"),
         "n_trades": 13, "profit_factor": 3.71, "total_pnl": 753.0},
        {"run_id": "22222222-2222-2222-2222-222222222222", "label": "w12",
         "status": "running", "created_at": pd.Timestamp("2026-07-05 11:00"),
         "finished_at": None, "n_trades": None, "profit_factor": None, "total_pnl": None},
    ])


def test_runs_page_renders_table_and_show_more(monkeypatch):
    calls = []

    def fake_runs_df(conn, limit=50, offset=0):
        calls.append(limit)
        return _runs_fixture()

    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", fake_runs_df)
    at = AppTest.from_function(_run_runs_page)
    at.run()
    assert not at.exception
    assert calls and calls[0] == 50          # default page size
    assert len(at.dataframe) >= 1            # the runs table rendered
    at.button(key="show_more").click().run()
    assert calls[-1] == 100                  # limit grew by 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_ui_pages.py -q`
Expected: new test FAILS (no `show_more` button yet).

- [ ] **Step 3: Implement**

Replace `runs_page` in `src/rs_spy/ui/pages.py`:

```python
_STATUS_ICONS = {"queued": "⏸", "running": "▶", "succeeded": "✅", "failed": "❌"}


def runs_page() -> None:
    st.title("Runs")
    if "runs_limit" not in st.session_state:
        st.session_state["runs_limit"] = 50

    @st.fragment(run_every="5s")
    def _runs_table() -> None:
        conn = data.get_conn()
        df = data.runs_df(conn, limit=st.session_state["runs_limit"])
        if df.empty:
            st.info("No runs yet — use Configure & Run.")
            return
        shown = df.assign(
            status=df["status"].map(lambda s: f"{_STATUS_ICONS.get(s, '?')} {s}")
        )
        event = st.dataframe(
            shown, hide_index=True,
            selection_mode="single-row", on_select="rerun", key="runs_table",
        )
        rows = event.selection.rows if event and event.selection else []
        if rows:
            st.session_state["selected_run_id"] = df.iloc[rows[0]]["run_id"]

    _runs_table()
    if st.button("Show more", key="show_more"):
        st.session_state["runs_limit"] += 50
        st.rerun()

    selected = st.session_state.get("selected_run_id")
    if selected:
        st.divider()
        render_run_detail(selected)


def render_run_detail(run_id) -> None:
    """Replaced with the full detail view in Task 3."""
    conn = data.get_conn()
    st.write(data.run_detail(conn, run_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_pages.py -q` — Expected: all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/pages.py tests/unit/test_ui_pages.py
git commit -m "M8: runs list page (5s status fragment, show-more, row select)"
```

---

### Task 3: Run detail (metrics, trades, equity, funnel, config, error)

**Files:**
- Modify: `src/rs_spy/ui/pages.py` (replace `render_run_detail`)
- Test: `tests/unit/test_ui_pages.py` (extend)

**Interfaces:**
- Consumes: `data.run_detail/trades_df/equity_series` (Task 1), `store/serialize.config_to_jsonb` (display form of the stored config).
- Produces: `render_run_detail(run_id)` — header (label + status), metrics `st.dataframe`
  (from the run row's `metrics` JSONB), equity `st.line_chart`, trades `st.dataframe`,
  funnel `st.dataframe` (from `runs.funnel`), the exact config JSON in an expander, the
  `error` text in `st.error` when failed, and a "Clone into Configure & Run" button that
  stores `st.session_state["clone_run_id"] = run_id` (consumed by Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_pages.py`:

```python
def _detail_fixture(status="succeeded", error=None):
    return {
        "run_id": "11111111-1111-1111-1111-111111111111", "label": "baseline",
        "status": status, "created_at": pd.Timestamp("2026-07-05 10:00"),
        "finished_at": pd.Timestamp("2026-07-05 10:20"), "error": error,
        "metrics": {"n_trades": 2, "profit_factor": 2.0, "total_pnl": 10.0},
        "funnel": {"eval_long": 100, "filled": 2},
        "config": {"rrs_m5_window": 18},
    }


def _run_detail_page():
    import streamlit as st
    from rs_spy.ui.pages import render_run_detail
    st.session_state.setdefault("noop", True)
    render_run_detail("11111111-1111-1111-1111-111111111111")


def test_run_detail_renders_metrics_trades_equity_and_funnel(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: _detail_fixture())
    monkeypatch.setattr(data, "trades_df", lambda conn, rid: pd.DataFrame(
        {"symbol": ["AAPL"], "pnl": [10.0]}))
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: pd.Series(
        [100.0, 110.0], index=pd.date_range("2026-07-01", periods=2, tz="UTC")))
    at = AppTest.from_function(_run_detail_page)
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 3   # metrics + trades + funnel tables


def test_run_detail_shows_error_for_failed_runs(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "run_detail",
                        lambda conn, rid: _detail_fixture("failed", error="boom"))
    monkeypatch.setattr(data, "trades_df", lambda conn, rid: pd.DataFrame())
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: None)
    at = AppTest.from_function(_run_detail_page)
    at.run()
    assert not at.exception
    assert any("boom" in e.value for e in at.error)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_ui_pages.py -q`
Expected: new tests FAIL (detail is still the raw-dict stub).

- [ ] **Step 3: Implement**

Replace `render_run_detail` in `src/rs_spy/ui/pages.py`:

```python
def render_run_detail(run_id) -> None:
    conn = data.get_conn()
    run = data.run_detail(conn, run_id)
    if run is None:
        st.warning(f"Run {run_id} not found.")
        return
    st.subheader(f"{run['label'] or run['run_id']} — {run['status']}")
    if run.get("error"):
        st.error(run["error"])

    metrics = run.get("metrics") or {}
    if metrics:
        st.dataframe(
            pd.DataFrame(sorted(metrics.items()), columns=["metric", "value"]),
            hide_index=True,
        )

    equity = data.equity_series(conn, run_id)
    if equity is not None and len(equity):
        st.line_chart(equity)

    trades = data.trades_df(conn, run_id)
    st.caption(f"{len(trades)} trades")
    if not trades.empty:
        st.dataframe(trades, hide_index=True)

    funnel = run.get("funnel") or {}
    if funnel:
        st.dataframe(
            pd.DataFrame(sorted(funnel.items()), columns=["counter", "count"]),
            hide_index=True,
        )

    with st.expander("Config (exact, as stored)"):
        st.json(run.get("config") or {})

    if st.button("Clone into Configure & Run", key=f"clone_{run_id}"):
        st.session_state["clone_run_id"] = run_id
        st.info("Open the Configure & Run page — the form is pre-seeded from this run.")
```

Add `import pandas as pd` to the imports of `src/rs_spy/ui/pages.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_pages.py -q` — Expected: all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/pages.py tests/unit/test_ui_pages.py
git commit -m "M8: run detail view (metrics, equity, trades, funnel, config, error)"
```

---

### Task 4: Configure & Run form (dataclass-driven, clone-and-tweak, launch)

**Files:**
- Create: `src/rs_spy/ui/form.py`
- Modify: `src/rs_spy/ui/pages.py` (replace `configure_page`)
- Test: `tests/unit/test_ui_form.py` (create), `tests/unit/test_ui_pages.py` (extend)

**Interfaces:**
- Consumes: `BacktestConfigM5` (dataclass), `data.create_and_launch`, `data.config_of`.
- Produces (`rs_spy/ui/form.py`, **pure — must not import streamlit**):
  `KNOWN_GATES = ["bias", "rrs", "rrs_m5", "vwap", "ha", "sma"]`;
  `DIP_HOLD_MODES = ["strict", "d1_session", "grace"]`;
  `ADVANCED_FIELDS = {"extra_symbols", "universe_file", "trade_symbols_override", "disabled_gates"}`;
  `field_specs(defaults: BacktestConfigM5) -> list[dict]` — one spec per dataclass field:
  `{"name", "kind" ("bool"|"int"|"float"|"str"|"choice"|"gates"|"symbols"), "value", "choices" (for choice), "advanced": bool}`
  (kind by `isinstance` on the default value; `dip_hold_mode` → choice/DIP_HOLD_MODES;
  `disabled_gates` → gates; tuple fields → symbols; advanced = membership in ADVANCED_FIELDS);
  `coerce(kind: str, raw) -> value` — `symbols`: comma-separated str → tuple; `gates`: list →
  frozenset; `int`/`float`/`bool`/`str`/`choice`: passthrough with type cast;
  `build_config(defaults: BacktestConfigM5, values: dict[str, object]) -> BacktestConfigM5`
  (`dataclasses.replace` over coerced values).
- Produces (`pages.py`): `configure_page()` — seeds defaults from
  `data.config_of(conn, st.session_state["clone_run_id"])` when set (else `BacktestConfigM5()`),
  renders a `st.form` (main fields; advanced ones inside an expander), label text input,
  and on submit calls `data.create_and_launch(conn, config, label)` and shows the run id.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ui_form.py`:

```python
"""Pure form helpers: field specs + coercion (no streamlit import)."""
import dataclasses

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.ui.form import (
    ADVANCED_FIELDS,
    DIP_HOLD_MODES,
    KNOWN_GATES,
    build_config,
    coerce,
    field_specs,
)


def test_form_module_does_not_import_streamlit():
    import rs_spy.ui.form as form
    assert "streamlit" not in getattr(form, "__dict__", {})
    assert not hasattr(form, "st")


def test_field_specs_cover_every_config_field_once():
    cfg = BacktestConfigM5()
    specs = field_specs(cfg)
    assert [s["name"] for s in specs] == [f.name for f in dataclasses.fields(cfg)]
    by_name = {s["name"]: s for s in specs}
    assert by_name["shorts_enabled"]["kind"] == "bool"
    assert by_name["rrs_m5_window"]["kind"] == "int"
    assert by_name["stop_atr_mult"]["kind"] == "float"
    assert by_name["dip_hold_mode"] == {
        "name": "dip_hold_mode", "kind": "choice", "value": cfg.dip_hold_mode,
        "choices": DIP_HOLD_MODES, "advanced": False,
    }
    assert by_name["disabled_gates"]["kind"] == "gates"
    assert by_name["extra_symbols"]["kind"] == "symbols"
    assert by_name["extra_symbols"]["advanced"] is True
    assert by_name["universe_file"]["kind"] == "str"


def test_coerce_symbols_and_gates():
    assert coerce("symbols", " AAPL, HOOD ,") == ("AAPL", "HOOD")
    assert coerce("symbols", "") == ()
    assert coerce("gates", ["bias", "sma"]) == frozenset({"bias", "sma"})
    assert coerce("int", "12") == 12
    assert coerce("float", 1.5) == 1.5
    assert coerce("bool", True) is True


def test_build_config_round_trips_defaults_and_applies_changes():
    cfg = BacktestConfigM5()
    specs = field_specs(cfg)
    values = {s["name"]: s["value"] for s in specs}
    assert build_config(cfg, values) == cfg          # untouched form == defaults
    values["rrs_m5_window"] = 24
    values["extra_symbols"] = "HOOD, SOFI"
    out = build_config(cfg, values)
    assert out.rrs_m5_window == 24
    assert out.extra_symbols == ("HOOD", "SOFI")


def test_known_gates_and_advanced_sets_are_the_spec_values():
    assert KNOWN_GATES == ["bias", "rrs", "rrs_m5", "vwap", "ha", "sma"]
    assert ADVANCED_FIELDS == {
        "extra_symbols", "universe_file", "trade_symbols_override", "disabled_gates",
    }
```

Append to `tests/unit/test_ui_pages.py`:

```python
def _run_configure_page():
    from rs_spy.ui.pages import configure_page
    configure_page()


def test_configure_page_submits_defaults_and_launches(monkeypatch):
    launched = []
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(
        data, "create_and_launch",
        lambda conn, config, label: launched.append((config, label)) or
        "33333333-3333-3333-3333-333333333333",
    )
    at = AppTest.from_function(_run_configure_page)
    at.run()
    assert not at.exception
    at.text_input(key="run_label").set_value("my-run")
    at.button(key="FormSubmitter:config_form-Run").click().run()
    assert not at.exception
    from rs_spy.backtest.engine_m5 import BacktestConfigM5
    config, label = launched[0]
    assert config == BacktestConfigM5()   # untouched form launches pure defaults
    assert label == "my-run"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_ui_form.py tests/unit/test_ui_pages.py -q`
Expected: FAIL with `ModuleNotFoundError: rs_spy.ui.form`.

- [ ] **Step 3: Implement**

Create `src/rs_spy/ui/form.py`:

```python
"""Config-form model: dataclass -> field specs -> widget values -> dataclass.

Pure module (NO streamlit import): pages render the specs with st widgets;
this module owns grouping, kinds, and coercion so it is unit-testable and the
form automatically tracks future BacktestConfigM5 fields (type-dispatched,
never a hand-maintained field list)."""
import dataclasses

from rs_spy.backtest.engine_m5 import BacktestConfigM5

KNOWN_GATES = ["bias", "rrs", "rrs_m5", "vwap", "ha", "sma"]
DIP_HOLD_MODES = ["strict", "d1_session", "grace"]
ADVANCED_FIELDS = {"extra_symbols", "universe_file", "trade_symbols_override", "disabled_gates"}


def field_specs(defaults: BacktestConfigM5) -> list[dict]:
    specs = []
    for f in dataclasses.fields(defaults):
        value = getattr(defaults, f.name)
        spec = {"name": f.name, "value": value, "advanced": f.name in ADVANCED_FIELDS}
        if f.name == "dip_hold_mode":
            spec |= {"kind": "choice", "choices": DIP_HOLD_MODES}
        elif f.name == "disabled_gates":
            spec |= {"kind": "gates", "value": sorted(value)}
        elif isinstance(value, bool):
            spec |= {"kind": "bool"}
        elif isinstance(value, int):
            spec |= {"kind": "int"}
        elif isinstance(value, float):
            spec |= {"kind": "float"}
        elif isinstance(value, tuple):
            spec |= {"kind": "symbols", "value": ", ".join(value)}
        else:
            spec |= {"kind": "str"}
        specs.append(spec)
    return specs


def coerce(kind: str, raw):
    if kind == "symbols":
        if isinstance(raw, tuple):
            return raw
        return tuple(s.strip() for s in str(raw).split(",") if s.strip())
    if kind == "gates":
        return frozenset(raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return bool(raw)
    return raw  # str / choice


def build_config(defaults: BacktestConfigM5, values: dict) -> BacktestConfigM5:
    kinds = {s["name"]: s["kind"] for s in field_specs(defaults)}
    coerced = {name: coerce(kinds[name], raw) for name, raw in values.items()}
    return dataclasses.replace(defaults, **coerced)
```

Replace `configure_page` in `src/rs_spy/ui/pages.py` (add
`from rs_spy.backtest.engine_m5 import BacktestConfigM5` and
`import rs_spy.ui.form as form` to its imports):

```python
def configure_page() -> None:
    st.title("Configure & Run")
    conn = data.get_conn()

    defaults = BacktestConfigM5()
    clone_id = st.session_state.get("clone_run_id")
    if clone_id:
        defaults = data.config_of(conn, clone_id)
        st.caption(f"Seeded from run {clone_id} (clone-and-tweak).")

    label = st.text_input("Run label", key="run_label")
    with st.form("config_form"):
        values: dict = {}
        main = [s for s in form.field_specs(defaults) if not s["advanced"]]
        advanced = [s for s in form.field_specs(defaults) if s["advanced"]]
        for spec in main:
            values[spec["name"]] = _widget(spec)
        with st.expander("Advanced (universe / gates / cohort overrides)"):
            for spec in advanced:
                values[spec["name"]] = _widget(spec)
        submitted = st.form_submit_button("Run")

    if submitted:
        config = form.build_config(defaults, values)
        run_id = data.create_and_launch(conn, config, label or None)
        st.success(f"Launched run {run_id} — watch it on the Runs page.")


def _widget(spec: dict):
    name, kind, value = spec["name"], spec["kind"], spec["value"]
    if kind == "bool":
        return st.checkbox(name, value=value, key=f"cfg_{name}")
    if kind == "int":
        return st.number_input(name, value=int(value), step=1, key=f"cfg_{name}")
    if kind == "float":
        return st.number_input(name, value=float(value), format="%.4f", key=f"cfg_{name}")
    if kind == "choice":
        idx = spec["choices"].index(value) if value in spec["choices"] else 0
        return st.selectbox(name, spec["choices"], index=idx, key=f"cfg_{name}")
    if kind == "gates":
        return st.multiselect(name, form.KNOWN_GATES, default=list(value), key=f"cfg_{name}")
    return st.text_input(name, value=str(value), key=f"cfg_{name}")  # str / symbols
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_form.py tests/unit/test_ui_pages.py -q`
Expected: all pass. Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/form.py src/rs_spy/ui/pages.py \
        tests/unit/test_ui_form.py tests/unit/test_ui_pages.py
git commit -m "M8: configure & run form (dataclass-driven, clone-and-tweak, launch)"
```

---

### Task 5: Compare page

**Files:**
- Modify: `src/rs_spy/ui/pages.py` (replace `compare_page`)
- Test: `tests/unit/test_ui_pages.py` (extend)

**Interfaces:**
- Consumes: `data.runs_df` (succeeded runs feed the picker), `data.run_detail`,
  `data.equity_series`.
- Produces: `compare_page()` — multiselect of succeeded runs by label; side-by-side
  metrics table (one column per run, metric rows aligned); overlaid equity chart where each
  curve is **rebased to 100 at its own start** (comparability across different capital paths).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_ui_pages.py`:

```python
def _run_compare_page():
    from rs_spy.ui.pages import compare_page
    compare_page()


def test_compare_page_renders_side_by_side_metrics(monkeypatch):
    runs = _runs_fixture()
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "runs_df", lambda conn, limit=200: runs)
    monkeypatch.setattr(data, "run_detail", lambda conn, rid: _detail_fixture())
    monkeypatch.setattr(data, "equity_series", lambda conn, rid: pd.Series(
        [200.0, 220.0], index=pd.date_range("2026-07-01", periods=2, tz="UTC")))
    at = AppTest.from_function(_run_compare_page)
    at.run()
    at.multiselect(key="compare_runs").set_value(["baseline"])
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 1   # the metrics comparison table
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_ui_pages.py -q`
Expected: FAIL (page is still the Task 1 stub; no `compare_runs` multiselect).

- [ ] **Step 3: Implement**

Replace `compare_page` in `src/rs_spy/ui/pages.py`:

```python
def compare_page() -> None:
    st.title("Compare")
    conn = data.get_conn()
    runs = data.runs_df(conn, limit=200)
    done = runs[runs["status"] == "succeeded"]
    if done.empty:
        st.info("No completed runs to compare yet.")
        return
    labels = done["label"].fillna(done["run_id"].astype(str)).tolist()
    picked = st.multiselect("Runs to compare", labels, key="compare_runs")
    if not picked:
        return
    chosen = done[done["label"].isin(picked)]

    cols = {}
    curves = {}
    for _, row in chosen.iterrows():
        run = data.run_detail(conn, row["run_id"]) or {}
        cols[row["label"]] = run.get("metrics") or {}
        eq = data.equity_series(conn, row["run_id"])
        if eq is not None and len(eq):
            curves[row["label"]] = eq / eq.iloc[0] * 100.0  # rebased to 100

    st.dataframe(pd.DataFrame(cols))
    if curves:
        st.line_chart(pd.DataFrame(curves))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_pages.py -q` — all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/pages.py tests/unit/test_ui_pages.py
git commit -m "M8: compare page (side-by-side metrics, rebased equity overlay)"
```

---

### Task 6: Scan & discovery page

**Files:**
- Modify: `src/rs_spy/ui/pages.py` (replace `scan_page`)
- Test: `tests/unit/test_ui_pages.py` (extend)

**Interfaces:**
- Consumes: `data.scan_dates/passing_history/scan_funnel/universe_snapshot/onboarded_df` (Task 1).
- Produces: `scan_page()` — passing-count history line chart; a date selectbox (newest first);
  that date's funnel as `st.metric` cards (assets / passed) + full funnel table; the
  `universe_snapshots` browser with a first-fail filter; the onboarded-symbols table with an
  `insufficient_history` badge column.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_ui_pages.py`:

```python
from datetime import date


def _run_scan_page():
    from rs_spy.ui.pages import scan_page
    scan_page()


def test_scan_page_renders_history_funnel_and_snapshot(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "scan_dates", lambda conn: [date(2026, 7, 2)])
    monkeypatch.setattr(data, "passing_history", lambda conn: pd.DataFrame(
        {"scan_date": [date(2026, 7, 2)], "n_passed": [1450]}))
    monkeypatch.setattr(data, "scan_funnel", lambda conn, d: {
        "assets": 14021, "passed": 1450, "fail_listing": 7030})
    monkeypatch.setattr(data, "universe_snapshot", lambda conn, d: pd.DataFrame(
        {"symbol": ["AAPL", "PENNY"], "passed": [True, False],
         "first_fail": [None, "price"]}))
    monkeypatch.setattr(data, "onboarded_df", lambda conn: pd.DataFrame(
        {"symbol": ["HOOD"], "insufficient_history": [False]}))
    at = AppTest.from_function(_run_scan_page)
    at.run()
    assert not at.exception
    assert len(at.metric) >= 2       # assets + passed cards
    assert len(at.dataframe) >= 2    # snapshot browser + onboarded table


def test_scan_page_with_no_scans_yet(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "scan_dates", lambda conn: [])
    at = AppTest.from_function(_run_scan_page)
    at.run()
    assert not at.exception
    assert at.info                    # friendly empty state, no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_ui_pages.py -q`
Expected: FAIL (stub page has no metrics/dataframes).

- [ ] **Step 3: Implement**

Replace `scan_page` in `src/rs_spy/ui/pages.py`:

```python
def scan_page() -> None:
    st.title("Scan & discovery")
    conn = data.get_conn()
    dates = data.scan_dates(conn)
    if not dates:
        st.info("No scans recorded yet — run scripts/run_nightly_scan.py.")
        return

    history = data.passing_history(conn)
    if not history.empty:
        st.line_chart(history.set_index("scan_date")["n_passed"])

    chosen = st.selectbox("Scan date", dates, key="scan_date")
    funnel = data.scan_funnel(conn, chosen) or {}
    if funnel:
        left, right = st.columns(2)
        left.metric("Assets evaluated", funnel.get("assets"))
        right.metric("Passed", funnel.get("passed"))
        st.dataframe(
            pd.DataFrame(sorted(funnel.items()), columns=["gate", "count"]),
            hide_index=True,
        )

    snapshot = data.universe_snapshot(conn, chosen)
    if not snapshot.empty:
        fails = ["(all)"] + sorted(snapshot["first_fail"].dropna().unique().tolist())
        pick = st.selectbox("Filter by first failing gate", fails, key="fail_filter")
        shown = snapshot if pick == "(all)" else snapshot[snapshot["first_fail"] == pick]
        st.caption(f"{len(shown)} symbols")
        st.dataframe(shown, hide_index=True)

    st.subheader("Onboarded symbols")
    onboarded = data.onboarded_df(conn)
    if onboarded.empty:
        st.caption("None yet.")
    else:
        st.dataframe(onboarded, hide_index=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_pages.py -q` — all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/pages.py tests/unit/test_ui_pages.py
git commit -m "M8: scan & discovery page (funnel, history, snapshot browser, onboarded)"
```

---

### Task 7: Campaign view (requires M10 Tasks 1–6 merged)

**Files:**
- Modify: `src/rs_spy/ui/pages.py` (replace `campaigns_page`), `src/rs_spy/ui/data.py` (add `campaign_groups`)
- Test: `tests/unit/test_ui_pages.py`, `tests/unit/test_ui_data.py` (extend)

**Interfaces:**
- Consumes: `data.parse_campaign_label` (Task 1); `rs_spy.backtest.aggregate.aggregate_campaign(conn, tag, variant) -> {"n_runs", "trades", "equity", "metrics"}` and `CampaignIncompleteError` (M10 plan Task 6); `repo.list_runs`.
- Produces: `data.campaign_groups(conn) -> pd.DataFrame` with one row per (tag, variant):
  columns `tag, variant, n_cohorts, statuses` (statuses = sorted unique list, e.g.
  `["succeeded"]` or `["running", "succeeded"]`), built from `list_runs(limit=500)` labels via
  `parse_campaign_label`; `campaigns_page()` — the groups table; picking a complete group
  (all-succeeded) renders `aggregate_campaign`'s pooled metrics + pooled equity chart;
  incomplete groups render a per-cohort status list instead (and `CampaignIncompleteError`
  is caught defensively around the aggregate call).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_data.py`:

```python
def test_campaign_groups_rolls_up_by_tag_and_variant(monkeypatch):
    import rs_spy.ui.data as data_mod
    rows = [
        {"label": "m10-jul05-baseline-c1", "status": "succeeded"},
        {"label": "m10-jul05-baseline-c2", "status": "running"},
        {"label": "m10-jul05-w12-c1", "status": "succeeded"},
        {"label": "onboarding-2026-07-06", "status": "succeeded"},  # not a campaign
        {"label": None, "status": "failed"},
    ]
    monkeypatch.setattr(data_mod.repo, "list_runs", lambda conn, limit=500: rows)
    df = data_mod.campaign_groups(None)
    assert len(df) == 2
    base = df[(df["tag"] == "jul05") & (df["variant"] == "baseline")].iloc[0]
    assert base["n_cohorts"] == 2
    assert base["statuses"] == ["running", "succeeded"]
```

Append to `tests/unit/test_ui_pages.py`:

```python
def _run_campaigns_page():
    from rs_spy.ui.pages import campaigns_page
    campaigns_page()


def test_campaigns_page_aggregates_complete_campaigns(monkeypatch):
    monkeypatch.setattr(data, "get_conn", lambda: None)
    monkeypatch.setattr(data, "campaign_groups", lambda conn: pd.DataFrame(
        [{"tag": "jul05", "variant": "baseline", "n_cohorts": 4,
          "statuses": ["succeeded"]}]))
    import rs_spy.ui.pages as pages_mod
    monkeypatch.setattr(pages_mod, "aggregate_campaign", lambda conn, tag, variant: {
        "n_runs": 4,
        "trades": pd.DataFrame({"symbol": ["AAPL"], "pnl": [10.0]}),
        "equity": pd.Series([400.0, 410.0],
                            index=pd.date_range("2026-07-01", periods=2, tz="UTC")),
        "metrics": {"n_trades": 40, "profit_factor": 2.5},
    })
    at = AppTest.from_function(_run_campaigns_page)
    at.run()
    at.selectbox(key="campaign_pick").set_value("jul05 / baseline")
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 2   # groups table + pooled metrics table
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_ui_data.py tests/unit/test_ui_pages.py -q`
Expected: FAIL (`campaign_groups` and the real page don't exist).

- [ ] **Step 3: Implement**

Append to `src/rs_spy/ui/data.py`:

```python
def campaign_groups(conn) -> pd.DataFrame:
    """One row per campaign (tag, variant) found in recent run labels."""
    rows = repo.list_runs(conn, limit=500)
    groups: dict[tuple, dict] = {}
    for r in rows:
        parsed = parse_campaign_label(r.get("label"))
        if parsed is None:
            continue
        tag, variant, _n = parsed
        g = groups.setdefault((tag, variant), {"n_cohorts": 0, "statuses": set()})
        g["n_cohorts"] += 1
        g["statuses"].add(r["status"])
    return pd.DataFrame([
        {"tag": t, "variant": v, "n_cohorts": g["n_cohorts"],
         "statuses": sorted(g["statuses"])}
        for (t, v), g in sorted(groups.items())
    ], columns=["tag", "variant", "n_cohorts", "statuses"])
```

Replace `campaigns_page` in `src/rs_spy/ui/pages.py` (add the import at module top:
`from rs_spy.backtest.aggregate import CampaignIncompleteError, aggregate_campaign`):

```python
def campaigns_page() -> None:
    st.title("Campaigns")
    conn = data.get_conn()
    groups = data.campaign_groups(conn)
    if groups.empty:
        st.info("No campaign runs found (labels m10-<tag>-<variant>-c<n>).")
        return
    st.dataframe(groups, hide_index=True)

    options = [f"{r.tag} / {r.variant}" for r in groups.itertuples()]
    pick = st.selectbox("Campaign", ["(choose)"] + options, key="campaign_pick")
    if pick == "(choose)":
        return
    tag, variant = (s.strip() for s in pick.split("/"))
    row = groups[(groups["tag"] == tag) & (groups["variant"] == variant)].iloc[0]

    if row["statuses"] != ["succeeded"]:
        st.warning(f"Campaign incomplete — cohort statuses: {row['statuses']}")
        return
    try:
        agg = aggregate_campaign(conn, tag, variant)
    except CampaignIncompleteError as e:
        st.warning(str(e))
        return
    st.caption(f"{agg['n_runs']} cohort runs pooled")
    st.dataframe(
        pd.DataFrame(sorted(agg["metrics"].items()), columns=["metric", "value"]),
        hide_index=True,
    )
    if agg["equity"] is not None:
        st.line_chart(agg["equity"])
    st.dataframe(agg["trades"], hide_index=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ui_data.py tests/unit/test_ui_pages.py -q` — all pass.
Full suite + `ruff check .` — green + clean.

- [ ] **Step 5: Commit**

```bash
git add src/rs_spy/ui/data.py src/rs_spy/ui/pages.py \
        tests/unit/test_ui_data.py tests/unit/test_ui_pages.py
git commit -m "M8: campaigns page (group by tag/variant, pooled metrics when complete)"
```

---

### Task 8: Docs + live smoke

- [ ] **Step 1: Live smoke against the real store** (Postgres up): `streamlit run app.py`;
  click through all five pages; launch one tiny real run from the form (label `m8-smoke`,
  defaults) and watch it go queued → running → succeeded on the Runs page (~20 min —
  verify the 5s fragment updates without manual refresh). Record any UX bug found and fix
  it in this task (small diffs only; anything structural becomes a follow-up).

- [ ] **Step 2: Docs.**
  - `CLAUDE.md`: add `ui/` to the codebase map, `streamlit run app.py` to the how-to-run
    table (note: `pip install -e ".[ui]"`).
  - `IMPLEMENTATION.md`: "M8: backtest UI" section — what was built (5 pages, out-of-process
    job model, fragment polling), what's deliberately out (real-time signals → discovery
    milestone #2, D1, study-suite triggering), and the stale-run caveat (a hard-killed job
    stays 'running'; the reaper query is documented in `jobs/launch.py`).
  - `README.md`: one "UI" paragraph with the run command.

- [ ] **Step 3: Final verification + commit**

Run: `python -m pytest -q && ruff check .` — green + clean.

```bash
git add CLAUDE.md IMPLEMENTATION.md README.md
git commit -m "M8: docs (UI section, codebase map, how-to-run)"
```

---

## Self-review notes (spec coverage)

- Job execution model (create_run queued → launch_run → poll PG) → Tasks 1 (data.create_and_launch), 4 (form submit). Never in-thread → constraint + design.
- Screens: Configure & Run (prefill defaults, clone-and-tweak via get_config, label, Run) → Task 4; Runs list (newest-first, status badge, headline metrics, auto-refresh) → Task 2; Run detail (trades, equity chart, metrics, funnel, exact config, error text) → Task 3; Compare (2+ completed, side-by-side + overlaid equity) → Task 5.
- Addendum: fragment 5s → Task 2; limit 50 + show-more → Task 2; st.line_chart only → Tasks 3/5/6/7; ui extras + AppTest testing → Task 1; scan & discovery page → Task 6; campaign view + aggregate → Task 7 (cross-plan dependency stated); advanced config fields incl. universe_file/trade_symbols_override/extra_symbols → Task 4 (ADVANCED_FIELDS).
- Stale-run flagging: documented in Task 8 docs (v1 shows status as stored; reaper query referenced) — the spec lists it under "future levers", not v1 scope.
- Out of scope honored: no real-time signals page, no D1, no study-suite triggering.
- Type consistency check: `data.runs_df(conn, limit, offset)` used by Tasks 2/5 matches Task 1; `_detail_fixture` shape matches `repo.get_run` row (dict with metrics/funnel/config JSONB); `aggregate_campaign` return shape matches M10 Task 6's contract; `parse_campaign_label` regex matches M10's label convention incl. dash-containing tags (greedy tag group + anchored variant/cohort).
