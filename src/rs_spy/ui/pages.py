"""Page render functions. Every data access goes through `data.<fn>(...)`."""
import pandas as pd
import streamlit as st

import rs_spy.ui.data as data

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
