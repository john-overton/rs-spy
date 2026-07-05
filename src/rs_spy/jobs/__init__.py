"""Detached backtest job execution for the runs-store.

`runner.run_job` executes one M5 backtest and records it to Postgres (status,
metrics, trades, equity), opening the DuckDB warehouse read-only so many jobs
run concurrently. `launch.launch_run` starts a job as a detached OS subprocess
that survives its parent (the future Streamlit UI, or a CLI) -- the parent then
polls the runs table for status. See scripts/run_backtest_job.py for the CLI.
"""
