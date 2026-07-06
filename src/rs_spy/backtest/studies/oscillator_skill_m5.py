"""M11 Phase 1: does a cycle oscillator's state read have forward-return skill?

Pure functions over supplied frames (hermetic tests use synthetic bars);
scripts/run_oscillator_study.py is the real-data shell. Forward-return
conventions match trigger_skill_m5: fwd = close.shift(-h)/close - 1, rows
without h bars of subsequent history are excluded from n (this lets horizons
cross session boundaries into the next session -- same disclosed convention
as the M7.5 trigger-skill study).

Train/holdout discipline (spec, pre-committed): TRAIN_END splits the windows;
selection happens on train only; the holdout driver evaluates exactly one
candidate, once.
"""
import pandas as pd

TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC")
HORIZONS = (12, 24, 78)
BULL_STATES = ("BULL_RUN", "BULL_EARLY")
BEAR_STATES = ("BEAR_RUN", "BEAR_EARLY")


def split_train_holdout(obj):
    """(train, holdout) by TRAIN_END. Raises if either side is empty."""
    train = obj[obj.index < TRAIN_END]
    holdout = obj[obj.index >= TRAIN_END]
    if len(train) == 0 or len(holdout) == 0:
        raise ValueError(
            f"empty window: train={len(train)} holdout={len(holdout)} rows "
            f"(TRAIN_END={TRAIN_END.date()})"
        )
    return train, holdout


def _fwd_stats(fwd: pd.Series, mask: pd.Series) -> dict:
    sub = fwd[mask & fwd.notna()]
    n = len(sub)
    return {
        "n": n,
        "mean_fwd_return": float(sub.mean()) if n else None,
        "median_fwd_return": float(sub.median()) if n else None,
    }


def state_skill_table(
    states: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    """One row per (state, horizon): n / mean / median forward return."""
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for state in (*BULL_STATES, *BEAR_STATES):
            rows.append(
                {"state": state, "horizon_bars": horizon,
                 **_fwd_stats(fwd, states == state)}
            )
    return pd.DataFrame(rows, dtype=object)


def separation_scores(table: pd.DataFrame) -> dict:
    """sep_h = n-weighted bull-state mean minus n-weighted bear-state mean.

    None when a horizon is absent or either side has zero observations.
    min_state_n = the smallest per-state, per-horizon observation count --
    the eligibility floors (train >=200, holdout >=50) bind against true
    state occupancy.
    """
    out: dict = {}
    horizons = sorted({int(h) for h in table["horizon_bars"]})
    for h in HORIZONS:
        if h not in horizons:
            out[f"sep_{h}"] = None
            continue
        sub = table[table["horizon_bars"] == h]

        def side_mean(names):
            side = sub[sub["state"].isin(names) & sub["mean_fwd_return"].notna()]
            n = side["n"].astype(float).sum()
            if n == 0:
                return None
            return float(
                (side["mean_fwd_return"].astype(float) * side["n"].astype(float)).sum() / n
            )

        bull, bear = side_mean(BULL_STATES), side_mean(BEAR_STATES)
        out[f"sep_{h}"] = None if bull is None or bear is None else bull - bear
    out["min_state_n"] = int(table["n"].astype(int).min()) if len(table) else 0
    return out


def cross_skill_table(
    crosses: pd.DataFrame, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        for event in crosses.columns:
            rows.append(
                {"event": event, "horizon_bars": horizon,
                 **_fwd_stats(fwd, crosses[event])}
            )
    return pd.DataFrame(rows, dtype=object)
