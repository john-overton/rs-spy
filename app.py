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
