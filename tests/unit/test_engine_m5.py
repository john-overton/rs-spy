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


def test_c8_survives_qqq_m5_index_genuinely_different_from_spy_m5():
    """Regression test for a real-market crash found while running M6's real
    backtest against the 5-year warehouse (128 symbols): c8 (QQQ agreement)
    computed `qqq_diff_pct = (qqq_m5["close"] - qqq_vwap) / qqq_vwap` without
    ever reindexing `qqq_m5["close"]` onto `spy_m5.index` first (unlike
    `qqq_vwap`, which IS correctly aligned via `align_causal`). Every fixture
    in this file's `_build` helper works around this by pre-reindexing/ffill
    QQQ's M5 bars onto SPY's M5 index before ever calling `compute_raw_score`
    -- and the docstring's "`qqq_m5` must share `spy_m5`'s index" contract
    was never enforced at runtime (flagged as a known limitation in M5's
    final review; see IMPLEMENTATION.md). On real market data QQQ and SPY's
    M5 bars are NOT identical (each can be missing bars the other has), so
    the raw pandas subtraction auto-aligns onto the UNION of both indices,
    `qqq_above = qqq_diff_pct > 0` ends up on that mismatched union index,
    and `spy_above == qqq_above` (strict equality between differently-indexed
    Series) raises `ValueError: Can only compare identically-labeled Series
    objects`.

    This builds a QQQ M5 index that is genuinely different from SPY's in
    both directions: missing several bars SPY has, AND carrying one
    timestamp SPY does NOT have (mirroring how two independently-traded real
    symbols' M5 bars can each be missing bars relative to the other,
    depending on real trade timing) -- so the union of the two indices is
    not just a superset/subset of either, which is exactly what triggers the
    ValueError above (a pure subset does not: pandas' auto-align union
    collapses back to spy_m5.index in that case, and the comparison
    silently "works" with NaN values, masking the bug)."""
    spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5_aligned = _build(n_sessions=5, bars_per_session=78, spy_drift=0.01)
    # _build() already reindexed/ffill'd qqq_m5 onto spy_m5.index -- undo that
    # here and instead pass QQQ's genuinely different native M5 index.
    qqq_m5_native = _m5_from_m1(qqq_m1)
    qqq_m5 = qqq_m5_native.drop(qqq_m5_native.index[10:15])  # QQQ missing 5 bars SPY has
    extra_ts = spy_m5.index[20] + pd.Timedelta(minutes=1)  # a QQQ bar timestamp SPY does NOT have
    extra_row = qqq_m5_native.iloc[[20]].copy()
    extra_row.index = pd.DatetimeIndex([extra_ts])
    qqq_m5 = pd.concat([qqq_m5, extra_row]).sort_index()

    assert not qqq_m5.index.equals(spy_m5.index), "fixture bug: qqq_m5 must have a genuinely different index"

    out = compute_raw_score(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)  # must not raise
    assert out["c8"].index.equals(spy_m5.index)
    assert out["c8"].isin([10.0, -10.0]).all()

    # bias_series (the higher-level entry point used by the real backtest)
    # must also run cleanly end-to-end.
    bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)


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
