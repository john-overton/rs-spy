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
