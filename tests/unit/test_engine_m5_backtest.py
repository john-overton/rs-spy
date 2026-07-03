import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5


def _m1_session(date: str, n_minutes: int, start_price: float, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{date} 09:30", periods=n_minutes, freq="1min", tz="America/New_York").tz_convert("UTC")
    noise = rng.normal(0, 0.05, n_minutes)
    close = start_price + np.cumsum(np.full(n_minutes, drift) + noise)
    high = close + np.abs(rng.normal(0.05, 0.02, n_minutes))
    low = close - np.abs(rng.normal(0.05, 0.02, n_minutes))
    open_ = close - drift - noise
    volume = rng.integers(500, 1500, n_minutes).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _build_m1(dates, n_minutes=390, start_price=100.0, drift=0.0, seed=1):
    frames = [_m1_session(d, n_minutes, start_price + i * drift * n_minutes, drift, seed + i) for i, d in enumerate(dates)]
    return pd.concat(frames)


def _build_d1(m1: pd.DataFrame) -> pd.DataFrame:
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_localize(None)
    return daily


# 30 trading-day-like sessions. Built via bdate_range (not a literal
# "2026-02-{02+i}" format string) because that naive formatting overflows
# February's 28 days partway through a 30-session run and produces invalid
# calendar dates (e.g. 2026-02-29..31).
DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=30)]


@pytest.fixture
def universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    qqq_m1 = _build_m1(DATES, drift=0.0006, seed=2)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)

    spy_m5 = resample_ohlcv(spy_m1, "5min")
    qqq_m5 = resample_ohlcv(qqq_m1, "5min")
    aapl_m5 = resample_ohlcv(aapl_m1, "5min")

    spy_d1 = _build_d1(spy_m1)
    qqq_d1 = _build_d1(qqq_m1)
    aapl_d1 = _build_d1(aapl_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "qqq_m1": qqq_m1, "qqq_m5": qqq_m5, "qqq_d1": qqq_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
    }


def test_prepare_m5_returns_calendar_matching_spy_m5_index(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.calendar.equals(universe["spy_m5"].index)
    assert "raw_score" in prepared.bias_df.columns
    assert "regime_d1_m5" not in prepared.bias_df.columns  # lives on PreparedM5 directly


def test_prepare_m5_per_symbol_outputs_are_reindexed_onto_the_master_calendar(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    for attr in ("bars", "features", "ema8", "atr_m5", "adv20_m5", "gate_long", "gate_short",
                 "score_long", "score_short", "rs_failure_long", "rs_failure_short",
                 "vwap_loss_long", "vwap_loss_short", "momentum_stall_long", "momentum_stall_short",
                 "confirm_trigger_long", "confirm_trigger_short", "dip_quality_long",
                 "bounce_quality_short", "squeeze_guard_short"):
        series_or_df = getattr(prepared, attr)["AAPL"]
        assert series_or_df.index.equals(prepared.calendar), f"{attr} not reindexed onto master calendar"


def test_prepare_m5_gates_are_bool_dtype_with_no_nan_after_reindex(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    gl = prepared.gate_long["AAPL"]
    assert gl.dtype == bool
    assert not gl.isna().any()


def test_prepare_m5_thin_symbol_computes_natively_before_reindex_onto_gappy_calendar(universe):
    """Regression test for the plan's Global Constraints invariant: every
    per-symbol M5-cadence quantity must be computed on that symbol's OWN
    native M5 index FIRST, and only the FINISHED outputs are reindexed onto
    the shared master calendar (SPY's M5 index) LAST, via a strict
    `.reindex(calendar)` (never `.ffill()`).

    The `universe` fixture above builds every symbol from the same dense,
    gap-free minute data over the same sessions, so aapl_m5.index and
    spy_m5.index end up bit-for-bit identical -- every `.reindex(calendar)`
    call in `_prepare_m5` is a no-op there, and a refactor that accidentally
    reindexed df_m5_native onto the master calendar *before* computing a
    rolling/EWM quantity on it (corrupting every value near the injected gap,
    not just the gap bar) would sail through undetected.

    This test builds a "THIN" symbol -- standing in for a lightly-traded,
    IEX-gap-prone name (see IMPLEMENTATION.md's BKNG-style coverage-gap
    notes) -- that trades on only every other session AND has a dropped
    chunk of minutes in the middle of each session it does trade. Its M5
    native index is therefore a genuine strict subset of SPY's M5 index, so
    the reindex calls in `_prepare_m5` do real work, and this test can check
    both sides of that work actually happened correctly.
    """
    from rs_spy.data.resample import resample_ohlcv
    from rs_spy.indicators.atr import atr as atr_fn

    thin_dates = DATES[::2]  # only half the sessions trade at all -> whole days missing from THIN's calendar
    gap_start, gap_end = 150, 250  # drop ~100 minutes from the middle of every session THIN does trade

    def _thin_session(date: str, i: int) -> pd.DataFrame:
        full = _m1_session(date, 390, 100.0 + i * 0.0007 * 390, 0.0007, seed=50 + i)
        return pd.concat([full.iloc[:gap_start], full.iloc[gap_end:]])

    thin_m1 = pd.concat([_thin_session(d, i) for i, d in enumerate(thin_dates)])
    thin_m5 = resample_ohlcv(thin_m1, "5min")
    thin_d1 = _build_d1(thin_m1)

    # Sanity-check the fixture itself: THIN's M5 index must be a genuine
    # strict subset of SPY's (this is what the `universe` fixture above does
    # NOT give us -- there, the two indices are identical and every reindex
    # call is a no-op).
    spy_m5_index = universe["spy_m5"].index
    assert set(thin_m5.index) < set(spy_m5_index)

    prepared = _prepare_m5(
        universe_m1={"THIN": thin_m1},
        universe_m5={"THIN": thin_m5},
        universe_d1={"THIN": thin_d1},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"THIN": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.calendar.equals(spy_m5_index)

    native_set = set(thin_m5.index)
    gap_timestamps = [t for t in prepared.calendar if t not in native_set]
    native_timestamps = [t for t in prepared.calendar if t in native_set]
    assert gap_timestamps, "fixture bug: expected some master-calendar bars THIN has no native bar for"
    assert native_timestamps, "fixture bug: expected some master-calendar bars THIN does have a native bar for"

    # (a) At master-calendar bars THIN has no native bar for, the reindexed
    # outputs must be NaN (float fields) / False (bool fields) -- never a
    # crash, and never a stale/incorrect non-NaN value.
    for t in gap_timestamps[:5] + gap_timestamps[-5:]:
        assert pd.isna(prepared.atr_m5["THIN"].loc[t])
        assert pd.isna(prepared.features["THIN"]["close"].loc[t])
        assert prepared.gate_long["THIN"].loc[t] == False  # noqa: E712
        assert prepared.gate_short["THIN"].loc[t] == False  # noqa: E712

    # (b) At a master-calendar bar THIN DOES have a native bar for, the
    # reindexed atr_m5/features must exactly match what computing directly on
    # THIN's own dense native M5 history produces. This is the crux of the
    # invariant: if `_prepare_m5` reindexed df_m5_native onto the (gappy,
    # relative to THIN) master calendar *before* computing ATR on it, the
    # injected NaN rows would corrupt the Wilder ATR recursion from the first
    # gap onward, and this value -- taken from deep into THIN's history, well
    # past several skipped sessions and intra-session gaps -- would no longer
    # match the reference value computed on THIN's own untouched native index.
    check_ts = native_timestamps[-1]
    expected_atr_native = atr_fn(thin_m5, n=14)
    assert check_ts in expected_atr_native.index

    actual_atr = prepared.atr_m5["THIN"].loc[check_ts]
    assert not pd.isna(actual_atr), "ATR must be a real float at a bar THIN actually has native data for"
    assert actual_atr == pytest.approx(expected_atr_native.loc[check_ts]), (
        "atr_m5 at a native bar must match ATR computed directly on THIN's own dense M5 "
        "history -- a mismatch means df_m5_native was reindexed onto the master calendar "
        "before ATR was computed on it, corrupting the rolling computation"
    )

    expected_close = thin_m5["close"].loc[check_ts]
    assert prepared.features["THIN"]["close"].loc[check_ts] == pytest.approx(expected_close)


def test_prepare_m5_regime_d1_m5_is_a_single_market_wide_series(universe):
    prepared = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert prepared.regime_d1_m5.index.equals(prepared.calendar)
