"""Page render functions. Every data access goes through `data.<fn>(...)`."""
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
    """Replaced with the full detail view in Task 3."""
    conn = data.get_conn()
    st.write(data.run_detail(conn, run_id))


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
