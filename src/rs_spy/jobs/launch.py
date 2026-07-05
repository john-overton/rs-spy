"""Launch a backtest job as a detached subprocess.

The pattern a UI (or CLI) uses to fire a run without blocking:

    run_id = create_run(conn, config, status='queued')   # row exists immediately
    launch_run(run_id)                                    # detached, returns at once
    # ... poll get_run(conn, run_id) for status until succeeded/failed ...

`start_new_session=True` puts the child in its own process group so it survives
the parent exiting (e.g. Streamlit restarting or the browser tab closing). The
parent does NOT wait() -- job status lives in Postgres, not the process tree.

Stale-run reaping (documented, not automated in v1): a hard SIGKILL/OOM can leave
a run stuck in 'running'. A UI can flag likely-dead runs with, e.g.:
    SELECT * FROM runs
    WHERE status='running' AND started_at < now() - interval '2 hours';
"""
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_JOB_SCRIPT = REPO_ROOT / "scripts" / "run_backtest_job.py"


def launch_run(run_id: uuid.UUID, *, log_path: Path | None = None) -> subprocess.Popen:
    """Start `scripts/run_backtest_job.py --run-id <run_id>` detached. Returns
    the Popen (mostly for the pid/log); the caller polls Postgres for status.

    The run row should already exist (status 'queued'); the job flips it to
    running/succeeded/failed. `log_path` captures the job's stdout+stderr.
    """
    stdout = open(log_path, "ab") if log_path is not None else subprocess.DEVNULL
    return subprocess.Popen(
        [sys.executable, str(_JOB_SCRIPT), "--run-id", str(run_id)],
        cwd=str(REPO_ROOT),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
