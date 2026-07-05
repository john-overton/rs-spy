"""Page render functions. Every data access goes through `data.<fn>(...)`."""
import pandas as pd
import streamlit as st

import rs_spy.ui.data as data
import rs_spy.ui.form as form
from rs_spy.backtest.aggregate import CampaignIncompleteError, aggregate_campaign
from rs_spy.backtest.engine_m5 import BacktestConfigM5

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


def compare_page() -> None:
    st.title("Compare")
    conn = data.get_conn()
    runs = data.runs_df(conn, limit=200)
    done = runs[runs["status"] == "succeeded"]
    if done.empty:
        st.info("No completed runs to compare yet.")
        return
    labels = done["label"].fillna(done["run_id"].astype(str))
    # labels are not unique in the store — disambiguate duplicates with a run_id prefix
    dup = labels.duplicated(keep=False)
    rid_by_display = {
        (f"{lbl} ({str(rid)[:8]})" if is_dup else lbl): rid
        for lbl, rid, is_dup in zip(labels, done["run_id"], dup)
    }
    picked = st.multiselect("Runs to compare", list(rid_by_display), key="compare_runs")
    if not picked:
        return

    cols = {}
    curves = {}
    for name in picked:
        run_id = rid_by_display[name]
        run = data.run_detail(conn, run_id) or {}
        cols[name] = run.get("metrics") or {}
        eq = data.equity_series(conn, run_id)
        if eq is not None and len(eq) and eq.iloc[0]:
            curves[name] = eq / eq.iloc[0] * 100.0  # rebased to 100

    st.dataframe(pd.DataFrame(cols))
    if curves:
        st.line_chart(pd.DataFrame(curves))


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
