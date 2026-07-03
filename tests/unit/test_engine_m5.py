import numpy as np
import pandas as pd

from rs_spy.bias.engine import bias_series, compute_raw_score
from rs_spy.bias.buckets import BULL, NEUTRAL, STRONG_BULL


def _m1_bars(n_sessions, bars_per_session, base, drift_per_bar=0.0, seed=1):
    rng = np.random.default_rng(seed)
    frames = []
    for s in range(n_sessions):
        day = pd.Timestamp("2024-06-03", tz="UTC") + pd.Timedelta(days=s)
        idx = pd.date_range(day.replace(hour=13, minute=30), periods=bars_per_session, freq="1min")
        close = base + np.arange(bars_per_session) * drift_per_bar + rng.normal(0, 0.03, bars_per_session)
        frames.append(
            pd.DataFrame(
                {
                    "open": close - 0.01,
                    "high": close + 0.03,
                    "low": close - 0.03,
                    "close": close,
                    "volume": rng.uniform(800, 1200, bars_per_session),
                },
                index=idx,
            )
        )
        base = close[-1]
    return pd.concat(frames)


def _m5_from_m1(m1: pd.DataFrame) -> pd.DataFrame:
    return m1.resample("5min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])


def _d1_from_m1(m1: pd.DataFrame) -> pd.DataFrame:
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    daily = daily.dropna(subset=["open"])
    daily.index = daily.index.tz_localize(None)
    return daily


def _build(n_sessions, bars_per_session, spy_drift, qqq_drift=None, seed=1):
    qqq_drift = spy_drift if qqq_drift is None else qqq_drift
    spy_m1 = _m1_bars(n_sessions, bars_per_session, 500.0, spy_drift, seed=seed)
    qqq_m1 = _m1_bars(n_sessions, bars_per_session, 400.0, qqq_drift, seed=seed + 1)
    spy_m5 = _m5_from_m1(spy_m1)
    qqq_m5 = _m5_from_m1(qqq_m1).reindex(spy_m5.index).ffill()
    spy_d1 = _d1_from_m1(spy_m1)
    return spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5


def test_compute_raw_score_has_all_components_and_warmup_flag():
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _build(n_sessions=25, bars_per_session=78, spy_drift=0.01)
    out = compute_raw_score(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    for col in [f"c{i}" for i in range(1, 9)] + ["raw_score", "warmup"]:
        assert col in out.columns
    assert out["raw_score"].abs().max() <= 100.0 + 1e-9


def test_warmup_true_before_1015_et_false_after():
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _build(n_sessions=5, bars_per_session=78, spy_drift=0.0)
    out = compute_raw_score(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    et = out.index.tz_convert("America/New_York")
    time_of_day = et - et.normalize()
    assert out.loc[time_of_day < pd.Timedelta(hours=10, minutes=15), "warmup"].all()
    assert not out.loc[time_of_day >= pd.Timedelta(hours=10, minutes=15), "warmup"].any()


def test_steady_uptrend_eventually_reaches_bull_or_strong_bull():
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _build(n_sessions=25, bars_per_session=78, spy_drift=0.02)
    out = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    assert out["bias"].iloc[-1] in (BULL, STRONG_BULL)


def test_flat_market_stays_neutral():
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5 = _build(n_sessions=15, bars_per_session=78, spy_drift=0.0)
    out = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    assert (out["bias"].dropna() == NEUTRAL).mean() > 0.5


def test_vwap_side_does_not_leak_the_next_minute_bar():
    # Regression test for a lookahead bug found while building Task 4
    # (selection/features_m5.py): M1 bars are open-labeled, M5 bars are
    # close-labeled (data/resample.py), so align_causal(vwap_on_m1, m5_index)
    # must NOT let an M5 bar labeled e.g. 13:35 see the M1 bar ALSO labeled
    # 13:35 (which covers [13:35, 13:36) and hasn't closed yet). Build a
    # single session where the last minute has a deliberate, large price
    # jump, and confirm an M5 bar closing exactly at that jump's start
    # doesn't have its VWAP side (c1) affected by it.
    idx = pd.date_range("2024-06-03 13:30", periods=15, freq="1min", tz="UTC")
    close = np.concatenate([np.full(9, 100.0), np.full(6, 200.0)])  # jump at minute 9 (13:39)
    spy_m1 = pd.DataFrame(
        {"open": close - 0.01, "high": close + 0.01, "low": close - 0.01, "close": close, "volume": 1000.0},
        index=idx,
    )
    qqq_m1 = spy_m1.copy()
    spy_m5 = _m5_from_m1(spy_m1)
    qqq_m5 = spy_m5.copy()
    spy_d1 = _d1_from_m1(spy_m1)

    out = compute_raw_score(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    # the M5 bar labeled 13:35 aggregates M1 bars 13:30..13:34 (all price
    # 100) and closes before the 13:39 jump -- its VWAP side must reflect
    # only the pre-jump price, not the jump.
    bar_1335 = out.loc[pd.Timestamp("2024-06-03 13:35", tz="UTC")]
    assert bar_1335["c1"] == 0.0  # close (100) sits right at its own VWAP (100) pre-jump
