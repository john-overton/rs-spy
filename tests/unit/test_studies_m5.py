import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5, _prepare_m5, run_m5_backtest
from rs_spy.backtest.studies.ablation_m5 import HARD_RULES_M5, _score_trades, run_gate_ablation_m5
from rs_spy.bias.buckets import BULL


def _m1_session(date, n_minutes, start_price, drift, seed):
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


def _build_d1(m1):
    daily = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_localize(None)
    return daily


DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=20)]


@pytest.fixture
def small_universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    qqq_m1 = _build_m1(DATES, drift=0.0006, seed=2)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)

    spy_m5, qqq_m5, aapl_m5 = resample_ohlcv(spy_m1, "5min"), resample_ohlcv(qqq_m1, "5min"), resample_ohlcv(aapl_m1, "5min")
    spy_d1, qqq_d1, aapl_d1 = _build_d1(spy_m1), _build_d1(qqq_m1), _build_d1(aapl_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "qqq_m1": qqq_m1, "qqq_m5": qqq_m5, "qqq_d1": qqq_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
    }


def test_run_gate_ablation_m5_returns_per_direction_summaries_and_run_counts(small_universe):
    u = small_universe
    config = BacktestConfigM5(shorts_enabled=True)
    universe_m1 = {"AAPL": u["aapl_m1"]}
    universe_m5 = {"AAPL": u["aapl_m5"]}
    universe_d1 = {"AAPL": u["aapl_d1"]}
    sectors = {"AAPL": "Technology"}

    baseline_prepared = _prepare_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )
    baseline_result = run_m5_backtest(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )

    result = run_gate_ablation_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, None, config, baseline_prepared, baseline_result,
    )

    assert result["run_trade_counts"]["baseline"] == len(baseline_result.trades)
    assert set(result["run_trade_counts"].keys()) == {"baseline", *[f"disable_{r}" for r in HARD_RULES_M5]}
    # Regardless of whether any trades exist in this tiny synthetic universe, the summary
    # frames must exist and be indexed over every possible rule_count 0..len(HARD_RULES_M5).
    if not result["trades"].empty:
        assert set(result["summary_long"]["rule_count"]) == set(range(len(HARD_RULES_M5) + 1))
        assert set(result["summary_short"]["rule_count"]) == set(range(len(HARD_RULES_M5) + 1))


def test_score_trades_rule_count_matches_hand_count_at_signal_bar(small_universe):
    """Targeted unit test for `_score_trades` itself (not just end-to-end
    plumbing): builds a real `PreparedM5` via `_prepare_m5`, hand-picks a
    calendar bar to serve as a trade's entry SIGNAL bar (one bar before the
    fabricated trade's `entry_time`, matching `_score_trades`'s own
    documented 1-bar-lag convention), independently reads each of the 6
    hard-gate functions/bias bucket at that exact bar from the prepared
    features, and asserts `_score_trades`'s own `rule_count` for a
    hand-crafted LONG `TradeM5` matches that independently-computed count.
    This would catch a bug like checking the wrong bar index or swapping a
    long/short gate function, which the end-to-end test above cannot detect
    since it never independently verifies `rule_count` values."""
    from rs_spy.backtest.engine_m5 import TradeM5
    from rs_spy.selection import gates

    u = small_universe
    universe_m1 = {"AAPL": u["aapl_m1"]}
    universe_m5 = {"AAPL": u["aapl_m5"]}
    universe_d1 = {"AAPL": u["aapl_d1"]}
    sectors = {"AAPL": "Technology"}
    config = BacktestConfigM5()

    prepared = _prepare_m5(
        universe_m1, universe_m5, universe_d1, u["spy_m1"], u["spy_m5"], u["spy_d1"],
        u["qqq_m1"], u["qqq_m5"], sectors, config=config,
    )
    calendar = prepared.calendar

    # Pick a signal bar comfortably inside the warmed-up region (well past
    # any rolling-window NaN warmup) so every gate function returns a real
    # boolean rather than a NaN-derived False by construction.
    signal_idx = len(calendar) // 2
    entry_idx = signal_idx + 1
    signal_time = calendar[signal_idx]
    entry_time = calendar[entry_idx]

    feat = prepared.features["AAPL"]
    expected = {
        "bias_ok": prepared.bias_df["bias"].iat[signal_idx] in (BULL, "STRONG_BULL"),
        "rrs_ok": bool(gates.gate_rrs_long(feat).iat[signal_idx]),
        "ha_ok": bool(gates.gate_ha_long(feat).iat[signal_idx]),
        "sma_ok": bool(gates.gate_sma_long(feat).iat[signal_idx]),
        "rrs_m5_ok": bool(gates.gate_rrs_m5_long(feat).iat[signal_idx]),
        "vwap_ok": bool(gates.gate_vwap_long(feat).iat[signal_idx]),
    }
    expected_rule_count = sum(expected.values())

    trade = TradeM5(
        symbol="AAPL", direction="LONG", entry_time=entry_time, entry_price=100.0,
        exit_time=calendar[entry_idx + 1], exit_price=101.0, shares=10.0,
        exit_reason="time_flat", pnl=10.0, r_multiple=1.0,
    )

    scored = _score_trades(prepared, [trade])
    assert len(scored) == 1
    row = scored.iloc[0]
    assert row["signal_time"] == signal_time
    assert row["rule_count"] == expected_rule_count
    for key, value in expected.items():
        assert bool(row[key]) == value
