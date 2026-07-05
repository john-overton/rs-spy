from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest import engine_m5
from rs_spy.backtest.engine_m5 import BacktestConfigM5, PreparedM5, _prepare_m5, run_m5_backtest
from rs_spy.bias.buckets import BEAR, BULL, NO_TRIGGER
from rs_spy.bias.regime import CHOP, TREND_UP
from rs_spy.selection import gates as gates_module


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


def test_short_history_extra_symbol_cannot_shrink_the_master_calendar(universe):
    """Spec guard (M9 onboarding): a newly onboarded symbol with short history
    must extend, never truncate, the shared picture. The master calendar is
    SPY's own M5 index, so adding a symbol that only traded the last 3
    sessions must leave the calendar bit-for-bit identical."""
    from rs_spy.data.resample import resample_ohlcv

    short_m1 = _build_m1(DATES[-3:], start_price=30.0, seed=77)
    short_m5 = resample_ohlcv(short_m1, "5min")
    short_d1 = _build_d1(short_m1)

    base = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        earnings_blackout=None,
        config=BacktestConfigM5(),
    )
    with_short = _prepare_m5(
        universe_m1={"AAPL": universe["aapl_m1"], "SHORTY": short_m1},
        universe_m5={"AAPL": universe["aapl_m5"], "SHORTY": short_m5},
        universe_d1={"AAPL": universe["aapl_d1"], "SHORTY": short_d1},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology", "SHORTY": "UNKNOWN"},
        earnings_blackout=None,
        config=BacktestConfigM5(),
    )
    assert with_short.calendar.equals(base.calendar)


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


def test_run_m5_backtest_produces_a_trade_log_and_equity_curve(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    assert result.equity_curve is not None
    assert len(result.equity_curve) > 0
    trades_df = result.trades_df()
    if not trades_df.empty:
        assert set(trades_df["exit_reason"].unique()) <= {
            "hard_stop", "trail_stop", "market_flip", "rs_failure", "vwap_loss",
            "profit_take", "time_flat", "squeeze_guard",
        }
        assert (trades_df["shares"] > 0).all()


def test_run_m5_backtest_never_exceeds_max_concurrent_long(universe):
    config = BacktestConfigM5(max_concurrent_long=1)
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=config,
    )
    trades_df = result.trades_df()
    if trades_df.empty:
        return
    events = []
    for _, t in trades_df.iterrows():
        events.append((t["entry_time"], 1))
        events.append((t["exit_time"], -1))
    events.sort()
    concurrent = 0
    for _, delta in events:
        concurrent += delta
        assert concurrent <= 1


def test_run_m5_backtest_shorts_disabled_by_default_produces_no_short_trades(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    if not trades_df.empty:
        assert (trades_df["direction"] == "LONG").all()


def test_run_m5_backtest_no_new_entries_before_1015_or_after_1530_et(universe):
    result = run_m5_backtest(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    if trades_df.empty:
        return
    et_times = trades_df["entry_time"].dt.tz_convert("America/New_York")
    tod = et_times - et_times.dt.normalize()
    assert (tod >= pd.Timedelta(hours=10, minutes=15)).all()
    assert (tod <= pd.Timedelta(hours=15, minutes=30)).all()


# --- Review-round-1 regression tests -----------------------------------------
#
# Both tests below monkeypatch `engine_m5._prepare_m5` to inject a fully
# hand-built `PreparedM5` instead of deriving one from realistic OHLCV data.
# `_prepare_m5`/its own dict-reindexing behavior is already covered by the
# tests above; these two tests are only exercising `run_m5_backtest`'s event
# loop itself, so a fully controlled, deterministic `PreparedM5` (rather than
# hoping realistic synthetic data happens to trip the exact conditions we
# need) is the more direct and reliable seam -- every other lever in the loop
# (bias, gates, scores, rrs/lrsi crossings, dip/bounce quality, ATR, EMA8) is
# real `run_m5_backtest` code operating on data we chose, not mocked-out
# behavior.


def _flat_series(calendar, value):
    return pd.Series(value, index=calendar)


def _build_prepared_for_run_loop(
    calendar,
    *,
    bias_by_bar,
    regime_by_bar,
    trigger_by_bar=None,
    bars_by_symbol,
    rrs_by_symbol,
    gate_long_by_symbol=None,
    score_long_by_symbol=None,
    dip_quality_long_by_symbol=None,
    ema8_by_symbol=None,
    atr_by_symbol=None,
    gate_short_by_symbol=None,
    score_short_by_symbol=None,
    bounce_quality_short_by_symbol=None,
    rs_failure_short_by_symbol=None,
    confirm_trigger_long_by_symbol=None,
    gate_long_hold_by_symbol=None,
) -> PreparedM5:
    """Hand-builds a `PreparedM5` for driving `run_m5_backtest`'s event loop
    directly, bypassing `_prepare_m5`'s (separately tested) derivation from
    raw OHLCV. Every per-symbol dict defaults to an all-False/0-valued Series
    for symbols not explicitly given a value, so callers only need to specify
    the levers a given scenario actually exercises."""
    symbols = list(bars_by_symbol)
    gate_long_by_symbol = gate_long_by_symbol or {}
    score_long_by_symbol = score_long_by_symbol or {}
    dip_quality_long_by_symbol = dip_quality_long_by_symbol or {}
    ema8_by_symbol = ema8_by_symbol or {}
    atr_by_symbol = atr_by_symbol or {}
    gate_short_by_symbol = gate_short_by_symbol or {}
    score_short_by_symbol = score_short_by_symbol or {}
    bounce_quality_short_by_symbol = bounce_quality_short_by_symbol or {}
    rs_failure_short_by_symbol = rs_failure_short_by_symbol or {}
    confirm_trigger_long_by_symbol = confirm_trigger_long_by_symbol or {}
    trigger_by_bar = trigger_by_bar if trigger_by_bar is not None else [NO_TRIGGER] * len(calendar)

    bias_df = pd.DataFrame(
        {
            "bias": list(bias_by_bar),
            "flip_flatten": [False] * len(calendar),
            "trigger": list(trigger_by_bar),
            "warmup": [False] * len(calendar),
        },
        index=calendar,
    )
    regime_d1_m5 = pd.Series(list(regime_by_bar), index=calendar)

    bars, features = {}, {}
    ema8, atr_m5, adv20_m5 = {}, {}, {}
    gate_long, gate_short, score_long, score_short = {}, {}, {}, {}
    rs_failure_long, rs_failure_short = {}, {}
    vwap_loss_long, vwap_loss_short = {}, {}
    momentum_stall_long, momentum_stall_short = {}, {}
    confirm_trigger_long, confirm_trigger_short = {}, {}
    dip_quality_long, bounce_quality_short, squeeze_guard_short = {}, {}, {}

    for sym in symbols:
        bars[sym] = bars_by_symbol[sym]
        features[sym] = pd.DataFrame(
            {
                "rolling_rrs_m5": rrs_by_symbol[sym],
                "lrsi_m5": _flat_series(calendar, 50.0),
            },
            index=calendar,
        )
        ema8[sym] = ema8_by_symbol.get(sym, _flat_series(calendar, 0.0))
        atr_m5[sym] = atr_by_symbol.get(sym, _flat_series(calendar, 1.0))
        adv20_m5[sym] = _flat_series(calendar, 50_000_000.0)
        gate_long[sym] = gate_long_by_symbol.get(sym, _flat_series(calendar, False))
        gate_short[sym] = gate_short_by_symbol.get(sym, _flat_series(calendar, False))
        score_long[sym] = score_long_by_symbol.get(sym, _flat_series(calendar, 0.0))
        score_short[sym] = score_short_by_symbol.get(sym, _flat_series(calendar, 0.0))
        rs_failure_long[sym] = _flat_series(calendar, False)
        rs_failure_short[sym] = rs_failure_short_by_symbol.get(sym, _flat_series(calendar, False))
        vwap_loss_long[sym] = _flat_series(calendar, False)
        vwap_loss_short[sym] = _flat_series(calendar, False)
        momentum_stall_long[sym] = _flat_series(calendar, False)
        momentum_stall_short[sym] = _flat_series(calendar, False)
        confirm_trigger_long[sym] = confirm_trigger_long_by_symbol.get(sym, _flat_series(calendar, False))
        confirm_trigger_short[sym] = _flat_series(calendar, False)
        dip_quality_long[sym] = dip_quality_long_by_symbol.get(sym, _flat_series(calendar, False))
        bounce_quality_short[sym] = bounce_quality_short_by_symbol.get(sym, _flat_series(calendar, False))
        squeeze_guard_short[sym] = _flat_series(calendar, False)

    hold_kwargs = {}
    if gate_long_hold_by_symbol is not None:
        hold_kwargs["gate_long_hold"] = {
            sym: gate_long_hold_by_symbol.get(sym, _flat_series(calendar, False)) for sym in symbols
        }
        hold_kwargs["gate_short_hold"] = {sym: _flat_series(calendar, False) for sym in symbols}

    return PreparedM5(
        calendar=calendar,
        bias_df=bias_df,
        regime_d1_m5=regime_d1_m5,
        bars=bars,
        features=features,
        ema8=ema8,
        atr_m5=atr_m5,
        adv20_m5=adv20_m5,
        gate_long=gate_long,
        gate_short=gate_short,
        score_long=score_long,
        score_short=score_short,
        rs_failure_long=rs_failure_long,
        rs_failure_short=rs_failure_short,
        vwap_loss_long=vwap_loss_long,
        vwap_loss_short=vwap_loss_short,
        momentum_stall_long=momentum_stall_long,
        momentum_stall_short=momentum_stall_short,
        confirm_trigger_long=confirm_trigger_long,
        confirm_trigger_short=confirm_trigger_short,
        dip_quality_long=dip_quality_long,
        bounce_quality_short=bounce_quality_short,
        squeeze_guard_short=squeeze_guard_short,
        **hold_kwargs,
    )


def test_long_trail_stop_locks_in_more_than_breakeven_as_trend_extends(monkeypatch):
    """Regression test for the inverted trailing-stop clamp (algo-spec 05
    §4.6/06 §4: "trail stop to max(EMA8(M5) - 0.25xATR_M5, entry)"). The bug
    computed `max(pos.stop, min(trail, pos.entry_price))`: once the trail
    trigger fires and price keeps extending favorably (the normal case this
    rule exists for), `min(trail, entry)` caps the candidate at breakeven
    forever, so the stop can never advance past entry even as EMA8 climbs far
    above it. This builds a symbol that trends up hard enough to trip the
    1.5xATR trail trigger, keeps extending for many more bars (so EMA8 climbs
    well past entry), then crashes through the stop -- the resulting
    hard-stop exit price is the trailed stop level itself (`min(pos.stop,
    bar_open)` with an intrabar-only crash, not a gap below it), so it
    directly reveals whether the stop actually advanced past breakeven.
    """
    sym = "TRND"
    n = 20
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")

    closes = [100.0, 100.5, 101.0, 101.2]
    closes += [101.2 + (k - 3) * 0.5 for k in range(4, 15)]  # sustained climb, i=4..14
    closes += [60.0] * (n - len(closes))  # crash at i=15, then flat
    opens = [c - 0.05 for c in closes]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.3 for c in closes]
    # i=15: an intrabar crash (NOT a gap below the trailed stop) so the
    # hard-stop fill (`min(pos.stop, bar_open)`) reflects the stop level
    # itself, not just wherever the bar happened to open.
    opens[15], highs[15], lows[15] = 106.5, 106.6, 55.0

    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    ema8_vals = [c - 1.0 for c in closes]  # a plausible EMA8 lagging just under price in an uptrend
    rrs_vals = [-1.0, 1.0] + [1.0] * (n - 2)  # crosses up at bar 1 -> arms the dip

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        ema8_by_symbol={sym: pd.Series(ema8_vals, index=calendar)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    assert not trades_df.empty, "expected the position to open and eventually hard-stop out"
    trade = trades_df.iloc[0]
    assert trade["direction"] == "LONG"
    assert trade["exit_reason"] == "trail_stop"
    # The crux of the regression: after the trend extends well past the
    # 1.5xATR trigger, the trailed stop must have locked in MEANINGFULLY more
    # than breakeven -- not frozen at (or below) entry, which is exactly what
    # the inverted `min(trail, entry)` clamp produced (exit price ends up
    # at/below entry, since the stop can never climb past it once triggered).
    assert trade["exit_price"] - trade["entry_price"] > 2.0, (
        f"exit_price {trade['exit_price']} was not meaningfully above entry_price "
        f"{trade['entry_price']} -- trailing stop appears frozen at breakeven"
    )


def test_slots_free_short_book_not_starved_by_open_long_positions(monkeypatch):
    """Regression test for the `slots_free`/`slots_free_s` cross-book
    miscounting bug: both used `len(positions)` (ALL open positions, across
    BOTH books) instead of counting only same-direction positions, so an open
    LONG position incorrectly consumed a slot from the SHORT book's own
    `max_concurrent_short` budget (and vice versa for the mirrored `slots_free`
    line).

    This builds two symbols: LNG goes long early (while bias is bullish) and
    is never given a reason to exit, so it stays open for the rest of the
    test -- occupying one "all positions" slot but zero actual short-book
    slots. SHT then arms its own short dip only once bias flips bearish
    (shorts require bearish bias to submit, by construction), reaching
    ENTRY_EVAL while LNG's long position is still open. With
    `max_concurrent_short=1`, the buggy `slots_free_s = 1 - len(positions) - ...`
    sees LNG's still-open long and computes `1 - 1 - 0 = 0` -- SHT's short
    order is never even submitted, and this test would see zero SHORT trades.
    The fixed formula counts only SHORT positions (`0`), correctly leaving
    `slots_free_s = 1`, and SHT's short goes through -- confirmed here by
    forcing a clean `rs_failure` exit afterward so a completed SHORT trade
    lands in the trade log for this test to assert on.

    (The mirrored direction -- an open SHORT starving the LONG book via the
    `slots_free` line -- is fixed identically for correctness/symmetry, but
    is not independently reachable through this engine's real control flow:
    a SHORT position's market-flip exit is unconditional on any bullish bar
    (see `short.py`'s documented asymmetry vs. the LONG side's flip_flatten
    -gated exit), while a new LONG submission requires that SAME bar (plus
    the one before it) to be bullish -- so any open SHORT is always flushed
    at least one bar before a LONG submission could ever see it in
    `positions`. Reintroducing both buggy lines together, as this test's own
    verification does, still exercises the reachable `slots_free_s` direction
    and fails as expected.)
    """
    n = 12
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    bias_by_bar = [BULL] * 6 + [BEAR] * 6

    # LNG: rrs crosses up at bar 1 -> DIP_ARMED -> ENTRY_EVAL at bar 2 -> fills
    # at bar 3 -> flat/gently-drifting price, never triggers any exit rule, so
    # it stays open through the rest of the test.
    lng_closes = [100.0 + i * 0.02 for i in range(n)]
    lng_bars = pd.DataFrame(
        {
            "open": [c - 0.02 for c in lng_closes],
            "high": [c + 0.15 for c in lng_closes],
            "low": [c - 0.15 for c in lng_closes],
            "close": lng_closes,
            "volume": [1_000.0] * n,
        },
        index=calendar,
    )
    lng_rrs = [-1.0, 1.0] + [1.0] * (n - 2)

    # SHT: rrs stays positive (never arms) while bias is still bullish, then
    # crosses down at bar 7 (bias has been bearish since bar 6) -> DIP_ARMED
    # -> ENTRY_EVAL at bar 8, submits/fills around bars 8-9, then a scripted
    # rs_failure at bar 10 forces a clean, deterministic exit.
    sht_closes = [50.0] * n
    sht_bars = pd.DataFrame(
        {
            "open": [49.95] * n,
            "high": [50.1] * n,
            "low": [49.8] * n,
            "close": sht_closes,
            "volume": [1_000.0] * n,
        },
        index=calendar,
    )
    sht_rrs = [1.0] * 7 + [-1.0] * (n - 7)
    sht_rs_failure = [False] * 10 + [True] * (n - 10)

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=bias_by_bar,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={"LNG": lng_bars, "SHT": sht_bars},
        rrs_by_symbol={"LNG": lng_rrs, "SHT": sht_rrs},
        gate_long_by_symbol={"LNG": _flat_series(calendar, True), "SHT": _flat_series(calendar, False)},
        score_long_by_symbol={"LNG": _flat_series(calendar, 100.0), "SHT": _flat_series(calendar, 0.0)},
        dip_quality_long_by_symbol={"LNG": _flat_series(calendar, True)},
        gate_short_by_symbol={"LNG": _flat_series(calendar, False), "SHT": _flat_series(calendar, True)},
        score_short_by_symbol={"LNG": _flat_series(calendar, 0.0), "SHT": _flat_series(calendar, 100.0)},
        bounce_quality_short_by_symbol={"SHT": _flat_series(calendar, True)},
        rs_failure_short_by_symbol={"SHT": pd.Series(sht_rs_failure, index=calendar)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    config = BacktestConfigM5(shorts_enabled=True, max_concurrent_long=5, max_concurrent_short=1)
    result = run_m5_backtest(
        universe_m1={}, universe_m5={"LNG": pd.DataFrame(), "SHT": pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={"LNG": "Technology", "SHT": "Energy"},
        config=config,
    )
    trades_df = result.trades_df()
    short_trades = trades_df[trades_df["direction"] == "SHORT"] if not trades_df.empty else trades_df
    assert not short_trades.empty, (
        "SHT's short was starved by LNG's open long position occupying a slot it should "
        "never have counted against -- slots_free_s must count only SHORT positions/pending orders"
    )
    assert (short_trades["symbol"] == "SHT").all()


def test_run_m5_backtest_disabled_bias_bypasses_the_long_market_bias_filter(monkeypatch):
    """Regression test for the M7 ablation-study bug: `disabled_gates={"bias"}`
    is currently a no-op in `run_m5_backtest` -- `bias_ok_long`/`bias_ok_short`
    are computed unconditionally from `prepared.bias_df`, never consulting
    `config.disabled_gates` (unlike `gates.gates_pass_long_m5`/
    `gates_pass_short_m5`, which DO receive `disabled=config.disabled_gates`
    for the rrs/ha/sma/vwap rules -- see lines 148/157). This builds a symbol
    whose watchlist state legitimately reaches ENTRY_EVAL, with every OTHER
    gate wide open, under a bias series that is BEAR on every single bar --
    so `bias_ok_long` is False throughout unless the "bias" ablation genuinely
    bypasses it (LONG's own market-flip exit is `flip_now`-gated, not a bare
    bias check, so a BEAR bias alone can't otherwise interfere with this
    scenario -- see the flip_flatten comment on the mirrored SHORT-side test
    below). With `disabled_gates=frozenset()`, no entry can ever be
    submitted, so trades must be empty. With `disabled_gates={"bias"}`, the
    same setup must submit + fill the entry and later hard-stop out --
    proving the ablation flag actually reached `bias_ok_long`, not just that
    trade counts happened to differ by chance.
    """
    sym = "LNG"
    n = 8
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")

    opens = [100.0] * n
    highs = [100.5] * n
    lows = [99.5] * n
    lows[4] = 98.0  # breach the 1xATR hard stop (entry 100.0 - 1.0*atr = 99.0) once open
    closes = [100.0] * n
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    rrs_vals = [-1.0, 1.0] + [1.0] * (n - 2)  # crosses up at bar1 -> QUALIFIED->DIP_ARMED->ENTRY_EVAL at bar2

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BEAR] * n,  # never bullish -> bias_ok_long is False throughout unless disabled
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    baseline = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    assert baseline.trades_df().empty, (
        "baseline (bias enabled) should never enter -- bias is BEAR on every bar, so "
        "bias_ok_long must be False throughout and no long entry can be submitted"
    )

    disabled_bias = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(disabled_gates=frozenset({"bias"})),
    )
    trades_df = disabled_bias.trades_df()
    assert not trades_df.empty, (
        "disabled_gates={'bias'} must bypass the market-bias filter and let the "
        "otherwise-qualified LONG entry through -- an empty result here means "
        "disabled_gates never reached bias_ok_long (the bug this test guards against)"
    )
    trade = trades_df.iloc[0]
    assert trade["direction"] == "LONG"
    assert trade["exit_reason"] == "hard_stop"


def test_run_m5_backtest_disabled_bias_bypasses_the_short_market_bias_filter(monkeypatch):
    """Mirrors the LONG-side test above for the SHORT side of the same bug --
    the M7 ablation study is genuinely bidirectional (unlike D1's, which is
    LONG-only), so the fix must cover both `bias_ok_long` AND `bias_ok_short`.

    This exercises the SHORT-side gate's extra `regime_d1_m5 != TREND_UP`
    clause specifically: bias is BEAR on every bar (so the plain bias check
    would normally pass) but regime is pinned to TREND_UP throughout, which
    the pre-fix code always applies unconditionally -- forcing
    `bias_ok_short` False the whole run regardless of the genuinely-bearish
    bias. `disabled_gates={"bias"}` must bypass BOTH clauses at once (the
    fixed branch replaces the whole expression with an unconditional
    `pd.Series(True, ...)`).

    Bias is held at BEAR (not BULL) so the open SHORT position itself isn't
    killed by the unconditional `bias_now in (BULL, STRONG_BULL)` market-flip
    check that SHORT positions carry (see
    `test_slots_free_short_book_not_starved_by_open_long_positions`'s
    docstring for that asymmetry) -- that check is orthogonal to this bug and
    would otherwise confound the result.
    """
    sym = "SHT"
    n = 8
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")

    opens = [100.0] * n
    highs = [100.5] * n
    lows = [99.5] * n
    highs[4] = 102.0  # breach the 1xATR hard stop (entry 100.0 + 1.0*atr = 101.0) once open
    closes = [100.0] * n
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    rrs_vals = [1.0, -1.0] + [-1.0] * (n - 2)  # crosses down at bar1 -> QUALIFIED->DIP_ARMED->ENTRY_EVAL at bar2

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BEAR] * n,
        regime_by_bar=[TREND_UP] * n,  # pins the SHORT-only regime exclusion on, every bar
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_short_by_symbol={sym: _flat_series(calendar, True)},
        score_short_by_symbol={sym: _flat_series(calendar, 100.0)},
        bounce_quality_short_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    shared_kwargs = dict(shorts_enabled=True, max_concurrent_short=1)
    baseline = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Energy"},
        config=BacktestConfigM5(**shared_kwargs),
    )
    assert baseline.trades_df().empty, (
        "baseline (bias enabled) should never enter SHORT -- regime is pinned to "
        "TREND_UP on every bar, so bias_ok_short must be False throughout and no "
        "short entry can be submitted, even though the raw bias is genuinely BEAR"
    )

    disabled_bias = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Energy"},
        config=BacktestConfigM5(disabled_gates=frozenset({"bias"}), **shared_kwargs),
    )
    trades_df = disabled_bias.trades_df()
    assert not trades_df.empty, (
        "disabled_gates={'bias'} must bypass the market-bias filter (including the "
        "TREND_UP exclusion) and let the otherwise-qualified SHORT entry through -- "
        "an empty result here means disabled_gates never reached bias_ok_short"
    )
    trade = trades_df.iloc[0]
    assert trade["direction"] == "SHORT"
    assert trade["exit_reason"] == "hard_stop"


def test_prepare_m5_threads_rrs_thresholds_into_long_gate_call(universe):
    config = BacktestConfigM5(
        rrs_m5_threshold_long=42.0, rrs_d1_threshold_long=43.0,
        rrs_m5_threshold_short=-44.0, rrs_d1_threshold_short=-45.0,
    )
    with patch(
        "rs_spy.backtest.engine_m5.gates.gates_pass_long_m5",
        wraps=gates_module.gates_pass_long_m5,
    ) as mock_long:
        _prepare_m5(
            universe_m1={"AAPL": universe["aapl_m1"]},
            universe_m5={"AAPL": universe["aapl_m5"]},
            universe_d1={"AAPL": universe["aapl_d1"]},
            spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
            qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
            sectors={"AAPL": "Technology"},
            config=config,
        )
    assert mock_long.called
    _, kwargs = mock_long.call_args
    assert kwargs["rrs_m5_threshold"] == 42.0
    assert kwargs["rrs_d1_threshold"] == 43.0


def test_prepare_m5_threads_rrs_thresholds_into_short_gate_call(universe):
    config = BacktestConfigM5(
        rrs_m5_threshold_long=42.0, rrs_d1_threshold_long=43.0,
        rrs_m5_threshold_short=-44.0, rrs_d1_threshold_short=-45.0,
    )
    with patch(
        "rs_spy.backtest.engine_m5.gates.gates_pass_short_m5",
        wraps=gates_module.gates_pass_short_m5,
    ) as mock_short:
        _prepare_m5(
            universe_m1={"AAPL": universe["aapl_m1"]},
            universe_m5={"AAPL": universe["aapl_m5"]},
            universe_d1={"AAPL": universe["aapl_d1"]},
            spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
            qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
            sectors={"AAPL": "Technology"},
            config=config,
        )
    assert mock_short.called
    _, kwargs = mock_short.call_args
    assert kwargs["rrs_m5_threshold"] == -44.0
    assert kwargs["rrs_d1_threshold"] == -45.0


def test_run_m5_backtest_threads_stop_atr_mult_into_stop_price_calls(monkeypatch):
    """BacktestConfigM5.stop_atr_mult must reach risk.stop_price_long as the
    stop_atr_mult argument -- a config knob that is accepted but never threaded
    would silently run every sweep cell at the 1.0 default."""
    from rs_spy.algo import risk as risk_module

    sym = "KNOB"
    n = 8
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    closes = [100.0] * n
    bars_df = pd.DataFrame(
        {
            "open": [c - 0.02 for c in closes],
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "volume": [1_000.0] * n,
        },
        index=calendar,
    )
    rrs_vals = [-1.0, 1.0] + [1.0] * (n - 2)  # crosses up at bar 1 -> arms the dip
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    with patch.object(engine_m5.risk, "stop_price_long", wraps=risk_module.stop_price_long) as spy:
        run_m5_backtest(
            universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
            spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
            qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
            sectors={sym: "Technology"},
            config=BacktestConfigM5(stop_atr_mult=2.0),
        )
    assert spy.called, "expected at least one long stop-price computation"
    assert all(call.kwargs.get("stop_atr_mult") == 2.0 for call in spy.call_args_list)


def test_dip_arm_cross_uses_symbols_last_native_reading_across_a_gap_bar(monkeypatch):
    """Regression test for IMPLEMENTATION.md known-limitation #23: the dip-arm
    RRS/LRSI cross detection read its "previous" value from the immediately
    preceding MASTER-calendar row of the reindexed features frame. For a
    thin/gappy symbol (a real, anticipated case on the IEX-only feed) that row
    is NaN whenever the symbol had no native bar there, so a genuine
    dip-and-recover (RRS -1.0 -> [gap] -> +1.0) never armed: NaN < 0 is False.

    The gate series here is hand-held True through the gap bar (production
    gates read False on gap bars, which ALSO demotes the symbol -- that is the
    matrix's Round-1 alert-model redesign scope, deliberately not this fix).
    This test pins cross-detection in isolation via the hand-built seam: with
    the fix, prev at the post-gap bar is the symbol's last REAL reading (-1.0),
    the cross fires, and a trade results; without it, the symbol sits QUALIFIED
    forever and the trade log is empty."""
    sym = "THIN"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")

    closes = [100.0] * 6 + [90.0] * (n - 6)  # crash at bar 6 forces a clean hard-stop exit
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    lows[6] = 89.0  # intrabar drop through the stop
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )

    # bar 0: -1.0 (real reading); bar 1: NaN (the symbol has no native bar --
    # a master-calendar gap row); bar 2: +1.0 (real reading again).
    rrs_vals = [-1.0, np.nan, 1.0] + [1.0] * (n - 3)

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    assert not trades_df.empty, (
        "RRS crossed up over a gap bar (-1.0 -> NaN gap -> +1.0) but no trade resulted -- "
        "the dip-arm 'previous' value must be the symbol's last real native reading, not the NaN gap row"
    )
    assert trades_df.iloc[0]["direction"] == "LONG"
    assert trades_df.iloc[0]["exit_reason"] == "hard_stop"


def _funnel_scenario_bars(n, calendar):
    closes = [100.0] * 6 + [90.0] * (n - 6)
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    if n > 6:  # bar-6 crash candle only exists in the n>=7 scenarios; n=6 callers use a crash-free fixture
        lows[6] = 89.0
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )


def test_funnel_counts_the_trigger_bypass_path_end_to_end(monkeypatch):
    """A QUALIFIED symbol on a LONG_TRIGGER bar with bias held 2+ bars must
    show up at every funnel stage: coincidence -> bypass -> submitted -> filled."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TRIG"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},  # never dips -> Path B can never fire; only the bypass can
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        confirm_trigger_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    f = result.funnel
    assert f["long_qualified_signals"] == 1  # IDLE -> QUALIFIED once, at bar 0
    assert f["long_dip_armed"] == 0
    assert f["long_trigger_bars"] == 1
    assert f["long_trigger_coincidences"] == 1
    assert f["long_trigger_killed_by_bias_hold"] == 0
    assert f["long_trigger_bypass"] == 1
    assert f["long_orders_submitted"] == 1
    assert f["long_orders_filled"] == 1
    assert not result.trades_df().empty


def test_funnel_counts_a_trigger_coincidence_killed_by_the_bias_two_bar_hold(monkeypatch):
    """Matrix thesis #3: a fresh trigger firing on the FIRST bullish bar fails
    bias_ok_long's 2-consecutive-bar hold. The funnel must record the
    coincidence AND attribute the kill to the bias hold. Pins bias_hold_bars=2
    explicitly (the spec's original hold) since the config default was promoted
    to 1 after the M7.5 robustness pass."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TRIG"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    bias_by_bar = [BEAR, BEAR, BEAR] + [BULL] * (n - 3)
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER  # first BULL bar: family holds only 1 bar -> bias_ok_long is False

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=bias_by_bar,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        confirm_trigger_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(bias_hold_bars=2),
    )
    f = result.funnel
    assert f["long_trigger_bars"] == 1
    assert f["long_trigger_coincidences"] == 1
    assert f["long_trigger_killed_by_bias_hold"] == 1
    assert f["long_trigger_bypass"] == 0
    assert f["long_orders_submitted"] == 0
    assert result.trades_df().empty


def test_funnel_counts_a_trigger_coincidence_killed_by_a_failing_full_gate(monkeypatch):
    """Important finding: in dip_hold_mode='d1_session', a symbol can be
    QUALIFIED (held via the relaxed hold gate) while its full gate is False.
    On a trigger bar with bias held, apply_trigger_bypass is then a no-op
    (gate_pass=False) and the coincidence must be attributed to
    trigger_killed_by_gate, not silently dropped -- the funnel partition
    trigger_coincidences == killed_by_bias_hold + killed_by_gate + bypass
    must hold in every mode."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TGATE"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER
    # full gate True on bars 0-1 (so IDLE -> QUALIFIED can happen), False from
    # bar 2 onward (including the trigger bar); hold gate always True so the
    # symbol stays QUALIFIED through the full-gate failure instead of
    # demoting to IDLE.
    full_gate = [True, True] + [False] * (n - 2)
    hold_gate = [True] * n

    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},  # flat: never dips, so only the bypass path is exercised
        gate_long_by_symbol={sym: pd.Series(full_gate, index=calendar)},
        gate_long_hold_by_symbol={sym: pd.Series(hold_gate, index=calendar)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(dip_hold_mode="d1_session"),
    )
    f = result.funnel
    assert f["long_trigger_bars"] == 1
    assert f["long_trigger_coincidences"] == 1
    assert f["long_trigger_killed_by_bias_hold"] == 0
    assert f["long_trigger_killed_by_gate"] == 1
    assert f["long_trigger_bypass"] == 0
    assert (
        f["long_trigger_coincidences"]
        == f["long_trigger_killed_by_bias_hold"] + f["long_trigger_killed_by_gate"] + f["long_trigger_bypass"]
    )
    assert result.trades_df().empty


def test_funnel_is_present_and_all_zero_when_nothing_ever_qualifies(monkeypatch):
    sym = "DEAD"
    n = 6
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, False)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    expected_keys = {
        f"{side}_{key}"
        for side in ("long", "short")
        for key in (
            "qualified_signals", "dip_armed", "entry_eval_via_dip",
            "trigger_bars", "trigger_coincidences", "trigger_killed_by_bias_hold",
            "trigger_killed_by_gate", "trigger_bypass",
            "eval_blocked_no_entry_window", "eval_blocked_risk_halt", "eval_blocked_bias",
            "eval_killed_by_lockout_or_cap", "eval_killed_by_quality", "eval_killed_by_ranking",
            "eval_killed_by_slots", "eval_killed_by_sizing",
            "orders_submitted", "orders_filled", "orders_cancelled_unfilled",
        )
    }
    assert set(result.funnel) == expected_keys
    assert all(v == 0 for v in result.funnel.values())


def test_run_m5_backtest_accepts_a_prebuilt_prepared_and_skips_prepare(monkeypatch):
    """Known-limitation #24: passing prepared= must skip the ~15-20 minute
    _prepare_m5 recompute entirely."""
    sym = "PREP"
    n = 6
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, False)},
    )

    def _explode(*a, **k):
        raise AssertionError("_prepare_m5 must not be called when prepared= is supplied")

    monkeypatch.setattr(engine_m5, "_prepare_m5", _explode)
    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
        prepared=prepared,
    )
    assert result.trades_df().empty  # gate is False throughout; the point is it ran at all


def test_run_m5_backtest_with_prepared_reproduces_the_from_scratch_result(universe):
    """Same config + same data: run_m5_backtest(prepared=...) must be
    bit-for-bit identical to letting it call _prepare_m5 itself."""
    config = BacktestConfigM5()
    kwargs = dict(
        universe_m1={"AAPL": universe["aapl_m1"]},
        universe_m5={"AAPL": universe["aapl_m5"]},
        universe_d1={"AAPL": universe["aapl_d1"]},
        spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
        qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
        sectors={"AAPL": "Technology"},
        config=config,
    )
    prepared = _prepare_m5(
        universe_m1=kwargs["universe_m1"], universe_m5=kwargs["universe_m5"],
        universe_d1=kwargs["universe_d1"],
        spy_m1=kwargs["spy_m1"], spy_m5=kwargs["spy_m5"], spy_d1=kwargs["spy_d1"],
        qqq_m1=kwargs["qqq_m1"], qqq_m5=kwargs["qqq_m5"],
        sectors=kwargs["sectors"], config=config,
    )
    r_shared = run_m5_backtest(**kwargs, prepared=prepared)
    r_scratch = run_m5_backtest(**kwargs)
    pd.testing.assert_series_equal(r_shared.equity_curve, r_scratch.equity_curve)
    pd.testing.assert_frame_equal(r_shared.trades_df(), r_scratch.trades_df())
    assert r_shared.funnel == r_scratch.funnel


def test_trailing_stop_exit_is_labeled_trail_stop_and_does_not_lock_out(monkeypatch):
    """A stop exit AFTER the 1.5xATR trail trigger armed must be labeled
    trail_stop (not hard_stop); the lockout/stop-out-counter exemption follows
    from RiskManager/lockout keying on the 'hard_stop' string (see engine exit
    processing), not asserted here. Reuses the trail-test price path: trend up
    hard, then crash."""
    sym = "TRND"
    n = 20
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    closes = [100.0, 100.5, 101.0, 101.2]
    closes += [101.2 + (k - 3) * 0.5 for k in range(4, 15)]
    closes += [60.0] * (n - len(closes))
    opens = [c - 0.05 for c in closes]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.3 for c in closes]
    opens[15], highs[15], lows[15] = 106.5, 106.6, 55.0
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    ema8_vals = [c - 1.0 for c in closes]
    rrs_vals = [-1.0, 1.0] + [1.0] * (n - 2)
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        ema8_by_symbol={sym: pd.Series(ema8_vals, index=calendar)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    assert not trades_df.empty
    trade = trades_df.iloc[0]
    assert trade["exit_reason"] == "trail_stop"
    assert trade["exit_price"] - trade["entry_price"] > 2.0  # trailed well past breakeven


def test_stop_exit_without_trail_arming_stays_hard_stop(monkeypatch):
    """The same-bar stop-out scenario (never any favorable excursion) must
    still be labeled hard_stop."""
    sym = "FLAT"
    n = 8
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [-1.0, 1.0] + [1.0] * (n - 2)},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)
    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(),
    )
    trades_df = result.trades_df()
    assert not trades_df.empty
    assert trades_df.iloc[0]["exit_reason"] == "hard_stop"


def test_bias_hold_bars_one_admits_a_first_bull_bar_trigger(monkeypatch):
    """Round 4 lever A3: with bias_hold_bars=1, a trigger firing on the FIRST
    bullish bar (which the default 2-bar hold kills -- see
    test_funnel_counts_a_trigger_coincidence_killed_by_the_bias_two_bar_hold)
    must convert to a trade."""
    from rs_spy.bias.buckets import LONG_TRIGGER

    sym = "TRIG"
    n = 10
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    bias_by_bar = [BEAR, BEAR, BEAR] + [BULL] * (n - 3)
    trigger_by_bar = [NO_TRIGGER] * n
    trigger_by_bar[3] = LONG_TRIGGER
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=bias_by_bar,
        regime_by_bar=[CHOP] * n,
        trigger_by_bar=trigger_by_bar,
        bars_by_symbol={sym: _funnel_scenario_bars(n, calendar)},
        rrs_by_symbol={sym: [1.0] * n},
        gate_long_by_symbol={sym: _flat_series(calendar, True)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        confirm_trigger_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)
    result = run_m5_backtest(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
        config=BacktestConfigM5(bias_hold_bars=1),
    )
    f = result.funnel
    assert f["long_trigger_killed_by_bias_hold"] == 0
    assert f["long_trigger_bypass"] == 1
    assert not result.trades_df().empty


def test_default_rrs_m5_window_is_18():
    """Promoted from the spec default 12 per the Rounds 2-3 sweep
    (docs/tuning/ledger.csv r23-w18-* rows: 10 trades / PF 2.06 vs 3 / 4.63)."""
    assert BacktestConfigM5().rrs_m5_window == 18


def test_prepare_m5_threads_confirm_knobs_into_confirm_trigger_calls(universe):
    from rs_spy.algo import long as long_algo_module

    config = BacktestConfigM5(rrs_m5_threshold_long=0.25, confirm_not_extended_atr_mult=1.75)
    with patch.object(engine_m5.long_algo, "confirm_trigger_entry_long",
                      wraps=long_algo_module.confirm_trigger_entry_long) as spy:
        _prepare_m5(
            universe_m1={"AAPL": universe["aapl_m1"]},
            universe_m5={"AAPL": universe["aapl_m5"]},
            universe_d1={"AAPL": universe["aapl_d1"]},
            spy_m1=universe["spy_m1"], spy_m5=universe["spy_m5"], spy_d1=universe["spy_d1"],
            qqq_m1=universe["qqq_m1"], qqq_m5=universe["qqq_m5"],
            sectors={"AAPL": "Technology"},
            config=config,
        )
    assert spy.called
    for call in spy.call_args_list:
        assert call.kwargs.get("rrs_m5_threshold") == 0.25
        assert call.kwargs.get("not_extended_atr_mult") == 1.75


def test_d1_session_dip_hold_mode_arms_and_trades_through_an_rrs_dip(monkeypatch):
    """Round 1 lever A1: with dip_hold_mode='d1_session', a QUALIFIED symbol
    whose rrs_m5 gate fails during the dip (full gate False, hold gate True)
    survives, arms on the RRS zero-cross, and converts to a Path B trade.
    Under the default strict mode this exact scenario produces zero trades
    (the dip bar demotes the symbol to IDLE)."""
    sym = "DIPR"
    n = 12
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    closes = [100.0] * 8 + [90.0] * (n - 8)
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    lows[8] = 89.0
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    # bar 0-1: strong (gates pass); bars 2-3: the dip -- rrs goes negative, FULL
    # gate fails; bar 4: rrs crosses back up through zero (arm) while the full
    # gate is still recovering
    rrs_vals = [1.5, 1.2, -0.8, -0.4, 0.6] + [1.0] * (n - 5)
    full_gate = [True, True, False, False, False, True] + [True] * (n - 6)
    hold_gate = [True] * n
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: pd.Series(full_gate, index=calendar)},
        gate_long_hold_by_symbol={sym: pd.Series(hold_gate, index=calendar)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)

    kwargs = dict(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
    )
    strict = run_m5_backtest(**kwargs, config=BacktestConfigM5())
    assert strict.trades_df().empty, "strict mode should demote at the dip and never trade"

    alert = run_m5_backtest(**kwargs, config=BacktestConfigM5(dip_hold_mode="d1_session"))
    assert not alert.trades_df().empty, "d1_session mode should survive the dip, arm on the cross, and trade"
    assert alert.funnel["long_dip_armed"] == 1


def test_grace_dip_hold_mode_tolerates_bounded_gate_failure(monkeypatch):
    """Round 1 lever A1 variant b: grace mode keeps QUALIFIED through a gate-fail
    streak shorter than dip_hold_grace_bars and demotes past it."""
    sym = "GRCE"
    n = 12
    calendar = pd.date_range("2026-03-02 09:30", periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    closes = [100.0] * 8 + [90.0] * (n - 8)
    opens = [c - 0.02 for c in closes]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.2 for c in closes]
    lows[8] = 89.0
    bars_df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000.0] * n}, index=calendar
    )
    rrs_vals = [1.5, 1.2, -0.8, -0.4, 0.6] + [1.0] * (n - 5)
    full_gate = [True, True, False, False, False, True] + [True] * (n - 6)
    prepared = _build_prepared_for_run_loop(
        calendar,
        bias_by_bar=[BULL] * n,
        regime_by_bar=[CHOP] * n,
        bars_by_symbol={sym: bars_df},
        rrs_by_symbol={sym: rrs_vals},
        gate_long_by_symbol={sym: pd.Series(full_gate, index=calendar)},
        score_long_by_symbol={sym: _flat_series(calendar, 100.0)},
        dip_quality_long_by_symbol={sym: _flat_series(calendar, True)},
        atr_by_symbol={sym: _flat_series(calendar, 1.0)},
    )
    monkeypatch.setattr(engine_m5, "_prepare_m5", lambda *a, **k: prepared)
    kwargs = dict(
        universe_m1={}, universe_m5={sym: pd.DataFrame()}, universe_d1={},
        spy_m1=pd.DataFrame(), spy_m5=pd.DataFrame(), spy_d1=pd.DataFrame(),
        qqq_m1=pd.DataFrame(), qqq_m5=pd.DataFrame(),
        sectors={sym: "Technology"},
    )
    wide = run_m5_backtest(**kwargs, config=BacktestConfigM5(dip_hold_mode="grace", dip_hold_grace_bars=6))
    assert not wide.trades_df().empty, "3-bar gate-fail streak within a 6-bar grace should survive and trade"

    tight = run_m5_backtest(**kwargs, config=BacktestConfigM5(dip_hold_mode="grace", dip_hold_grace_bars=2))
    assert tight.trades_df().empty, "3-bar streak past a 2-bar grace should demote before the cross"
