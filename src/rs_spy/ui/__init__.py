"""M8 backtest UI (Streamlit, local single-user).

Design: docs/superpowers/specs/backtest-ui.md (+ 2026-07-05 addendum).
Pages render; data.py talks to Postgres; jobs run out-of-process via
jobs/launch. The UI never executes a backtest in-process.
"""
