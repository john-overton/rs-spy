import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest import engine_m5
from rs_spy.backtest.engine_m5 import BacktestConfigM5, PreparedM5, _prepare_m5, run_m5_backtest
from rs_spy.bias.buckets import BEAR, BULL, NO_TRIGGER
from rs_spy.bias.regime import CHOP


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
            "hard_stop", "market_flip", "rs_failure", "vwap_loss",
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
        confirm_trigger_long[sym] = _flat_series(calendar, False)
        confirm_trigger_short[sym] = _flat_series(calendar, False)
        dip_quality_long[sym] = dip_quality_long_by_symbol.get(sym, _flat_series(calendar, False))
        bounce_quality_short[sym] = bounce_quality_short_by_symbol.get(sym, _flat_series(calendar, False))
        squeeze_guard_short[sym] = _flat_series(calendar, False)

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
    assert trade["exit_reason"] == "hard_stop"
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
