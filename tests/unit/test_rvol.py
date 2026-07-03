import numpy as np
import pandas as pd

from rs_spy.indicators.rvol import rvol


def _sessions_df(n_sessions: int, bar0_vols: list[float], bar1_vols: list[float]):
    rows = []
    index = []
    base = pd.Timestamp("2024-01-02 14:30", tz="UTC")
    day = pd.Timedelta(days=1)
    for i in range(n_sessions):
        d = base + i * day
        rows.append(bar0_vols[i])
        index.append(d)
        rows.append(bar1_vols[i])
        index.append(d + pd.Timedelta(minutes=1))
    return pd.DataFrame({"volume": rows}, index=pd.DatetimeIndex(index))


def test_rvol_matches_hand_computed_baseline():
    # 20 identical sessions (bar0=100, bar1=200 -> cumvol 100, 300), then a
    # 21st session with bar0=150. Expected baseline at bar_idx=0 is the mean
    # of the prior 20 sessions' cumvol at bar_idx=0 (=100), so RVOL=150/100=1.5.
    bar0 = [100.0] * 20 + [150.0]
    bar1 = [200.0] * 20 + [999.0]  # irrelevant for bar_idx=0 check
    df = _sessions_df(21, bar0, bar1)

    result = rvol(df)
    # bar_idx=0 rows are at even positions (0, 2, 4, ..., 40)
    session21_bar0_idx = 2 * 20
    assert result.iloc[session21_bar0_idx] == 1.5


def test_rvol_nan_before_lookback_satisfied():
    bar0 = [100.0] * 25
    bar1 = [200.0] * 25
    df = _sessions_df(25, bar0, bar1)
    result = rvol(df, lookback_sessions=20)
    # first 20 sessions can't have a full 20-session trailing baseline yet
    first_19_bar0_positions = [2 * i for i in range(19)]
    assert result.iloc[first_19_bar0_positions].isna().all()


def test_rvol_causal_smoke():
    """RVOL at session N must not change if future sessions are appended."""
    bar0 = [100.0] * 22
    bar1 = [200.0] * 22
    df = _sessions_df(22, bar0, bar1)
    full = rvol(df)

    truncated_df = df.iloc[:40]  # first 20 sessions only
    truncated = rvol(truncated_df)
    np.testing.assert_allclose(
        truncated.dropna().to_numpy(), full.iloc[: len(truncated)].dropna().to_numpy()
    )
