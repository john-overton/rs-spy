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

The holdout driver computes the oscillator on the holdout slice in isolation
(EMA state warms up from the first holdout bar rather than carrying 2024
state in). This is a deliberate, conservative choice: it slightly handicaps
the candidate at the window boundary but guarantees train information cannot
leak into the holdout read.
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


from rs_spy.bias.buckets import (  # noqa: E402
    BULL,
    LONG_TRIGGER,
    STRONG_BEAR,
    STRONG_BULL,
)
from rs_spy.bias.buckets import BEAR as BEAR_BUCKET  # noqa: E402

INCUMBENT_BULL = (STRONG_BULL, BULL)
INCUMBENT_BEAR = (STRONG_BEAR, BEAR_BUCKET)


def incumbent_skill(
    bias: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> tuple[pd.DataFrame, dict]:
    """Score the current bias engine's buckets with the oscillator's metric.

    Bucket -> state mapping: bull buckets -> BULL_RUN, bear buckets -> BEAR_RUN,
    NEUTRAL/other -> NaN (excluded). The EARLY states stay empty -- the
    incumbent has no equivalent; separation_scores handles the n=0 sides via
    the n-weighted means (weight 0)."""
    mapped = pd.Series(float("nan"), index=bias.index, dtype=object)
    mapped[bias.isin(INCUMBENT_BULL)] = "BULL_RUN"
    mapped[bias.isin(INCUMBENT_BEAR)] = "BEAR_RUN"
    table = state_skill_table(mapped, close, horizons)
    return table, separation_scores(table)


def trigger_composition_table(
    trigger: pd.Series, states: pd.Series, close: pd.Series, horizons: tuple = HORIZONS
) -> pd.DataFrame:
    """Forward returns of LONG_TRIGGER events, unconditioned (ALL) and
    conditioned on the oscillator state at the trigger bar -- the
    decision-relevant read, since real entries need trigger-in-window
    coincidence."""
    is_long = trigger == LONG_TRIGGER
    rows = []
    for horizon in horizons:
        fwd = close.shift(-horizon) / close - 1.0
        rows.append(
            {"state": "ALL", "horizon_bars": horizon, **_fwd_stats(fwd, is_long)}
        )
        for state in (*BULL_STATES, *BEAR_STATES):
            rows.append(
                {"state": state, "horizon_bars": horizon,
                 **_fwd_stats(fwd, is_long & (states == state))}
            )
    return pd.DataFrame(rows, dtype=object)


from rs_spy.indicators.cycle_oscillator import (  # noqa: E402
    OscSpec,
    compute_oscillator,
    oscillator_states,
)

TRAIN_MIN_STATE_N = 200
HOLDOUT_MIN_STATE_N = 50
_GRID_PAIRS = ((6, 13), (9, 21), (12, 26), (16, 36))
_GRID_SIGNALS = (5, 9, 13)


def candidate_grid() -> list[OscSpec]:
    """The 24 pre-committed candidates. Deliberately modest ('1OP is probably
    simple'); for vwap_dev the slow parameter is formula-unused bookkeeping."""
    return [
        OscSpec(mode, fast, slow, signal)
        for mode in ("close", "vwap_dev")
        for (fast, slow) in _GRID_PAIRS
        for signal in _GRID_SIGNALS
    ]


def run_train_sweep(
    m5: pd.DataFrame, specs: list[OscSpec]
) -> tuple[pd.DataFrame, OscSpec | None]:
    """Score every candidate on the TRAIN window only; pick the winner.

    Selection is pre-committed: highest sep_24 among candidates whose every
    state has n >= TRAIN_MIN_STATE_N; tie-break sep_12. Holdout data never
    enters this function's scoring (split happens here, not in the caller,
    so a caller mistake cannot leak holdout bars into selection).

    Uses a plain boundary filter rather than `split_train_holdout` directly:
    that helper's non-empty-both-sides invariant is right for a top-level
    train/holdout split but wrong here -- a sweep restricted to train data
    (e.g. a caller that has already isolated a train-only frame, as the
    hermetic tests do) must not be forced to fabricate holdout rows just to
    pass a sanity check that isn't this function's concern."""
    train_m5 = m5[m5.index < TRAIN_END]
    rows = []
    by_name: dict[str, OscSpec] = {}
    for spec in specs:
        osc = compute_oscillator(train_m5, spec)
        states = oscillator_states(osc)
        table = state_skill_table(states, train_m5["close"])
        scores = separation_scores(table)
        eligible = (
            scores["min_state_n"] >= TRAIN_MIN_STATE_N
            and scores["sep_24"] is not None
            and scores["sep_12"] is not None
        )
        rows.append(
            {"name": spec.name, "input_mode": spec.input_mode, "fast": spec.fast,
             "slow": spec.slow, "signal": spec.signal, **scores, "eligible": eligible}
        )
        by_name[spec.name] = spec
    results = pd.DataFrame(rows, dtype=object)
    eligible_rows = results[results["eligible"] == True]  # noqa: E712
    if eligible_rows.empty:
        return results, None
    top = eligible_rows.sort_values(
        ["sep_24", "sep_12"], ascending=[False, False]
    ).iloc[0]
    return results, by_name[top["name"]]


def holdout_verdict(
    winner_scores: dict, incumbent_scores: dict, train_sep_24: float
) -> dict:
    """The pre-committed hard gate (spec: 'no exceptions')."""
    sep_24 = winner_scores.get("sep_24")
    sep_12 = winner_scores.get("sep_12")
    inc_24 = incumbent_scores.get("sep_24")
    checks = {
        "sep_24_pos": sep_24 is not None and sep_24 > 0,
        "sep_12_pos": sep_12 is not None and sep_12 > 0,
        "beats_incumbent": (
            sep_24 is not None and (inc_24 is None or sep_24 > inc_24)
        ),
        "sign_consistent": (
            sep_24 is not None
            and train_sep_24 is not None
            and (sep_24 > 0) == (train_sep_24 > 0)
        ),
        "min_n_ok": winner_scores.get("min_state_n", 0) >= HOLDOUT_MIN_STATE_N,
    }
    return {"pass": all(checks.values()), "checks": checks}
