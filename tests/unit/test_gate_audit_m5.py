import numpy as np
import pandas as pd
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.backtest.studies.gate_audit_m5 import (
    run_gate_pass_audit,
    symbol_gate_rates,
    symbol_watchlist_reach,
)
from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL
from rs_spy.selection.watchlist import DIP_ARMED, ENTRY_EVAL, IDLE, QUALIFIED

_N = 10


def _gate_rates_fixture():
    """10-bar hand-built df/feat where every gate's pass/fail pattern is
    independently controlled, so joint pass counts can be derived by hand.

    Per-gate pass pattern (rows 0-9), long side:
      price:      T T T T T T T T T T   (100%)
      adv:        T T T T T F F F F F   (50%,  via adv20, NOT df["volume"])
      rrs_d1:     T T T T T T T F F F   (70%)
      ha:         T T T T T T T T F F   (80%)
      sma:        T T T T T T F F F F   (60%)
      headroom:   F F F T T T T T T T   (70%)
      volume_d1:  T T T T T T T T T T   (100%)
      rrs_m5:     T T T T T F F F F F   (50%)
      vwap:       T T T T F F F F F F   (40%)
      no_1candle: T T T T T T T T T F   (90%)
      no_gap:     T T T T T T T T F F   (80%)

    Only row 3 clears every long gate simultaneously (AND of the columns
    above at index 3 is all-True; every other row fails at least one gate).

    Short side reuses the same underlying columns with short-side
    thresholds/comparisons; adv (rows 0-4) and rrs_d1-short/ha-short/sma-
    short (all only True at rows 6-9 or later) never overlap, so NO row
    clears every short gate -- short_joint_pct is exactly 0.
    """
    idx = pd.RangeIndex(_N)
    df = pd.DataFrame(
        {
            "close": [50.0] * _N,
            # Deliberately large and constant: a naive rolling-mean-of-
            # own-bar-volume ADV gate would pass every single bar on this
            # volume. adv20 (below) is the genuine, mostly-failing ADV
            # series that symbol_gate_rates must actually honor.
            "volume": [2_000_000.0] * _N,
        },
        index=idx,
    )
    feat = pd.DataFrame(
        {
            "close": [50.0] * _N,
            "vwap_m5": [49.0] * 4 + [51.0] * 6,
            "rolling_rrs_m5": [2.0] * 5 + [-2.0] * 5,
            "rolling_rrs_d1": [2.0] * 7 + [-2.0] * 3,
            "ha_cont_d1": [3] * 8 + [-3] * 2,
            "sma_stack": [ABOVE_ALL] * 6 + [BELOW_ALL] * 4,
            "headroom_long": [0.5, 0.5, 0.5] + [None] * 7,
            "headroom_short": [2.0] * _N,
            "volume_ratio_d1": [5.0] * _N,
            "one_candle_wonder": [False] * 9 + [True],
            "gap_pct": [0.05] * 8 + [0.35] * 2,
        },
        index=idx,
    )
    adv20 = pd.Series([200_000.0] * 5 + [50_000.0] * 5, index=idx)
    config = BacktestConfigM5(min_adv_shares=100_000.0)
    return df, feat, adv20, config


def test_symbol_gate_rates_matches_hand_computed_percentages():
    df, feat, adv20, config = _gate_rates_fixture()
    row, gl, gs = symbol_gate_rates("TEST", df, feat, adv20, config)

    assert row["symbol"] == "TEST"
    assert row["n_native_bars"] == _N

    # long side per-gate pass rates
    assert row["long_price_pct"] == 100.0
    assert row["long_adv_pct"] == 50.0
    assert row["long_rrs_d1_pct"] == 70.0
    assert row["long_ha_pct"] == 80.0
    assert row["long_sma_pct"] == 60.0
    assert row["long_headroom_pct"] == 70.0
    assert row["long_volume_d1_pct"] == 100.0
    assert row["long_rrs_m5_pct"] == 50.0
    assert row["long_vwap_pct"] == 40.0
    assert row["long_not_one_candle_wonder_pct"] == 90.0
    assert row["long_no_gap_exclusion_pct"] == 80.0

    # only bar index 3 clears every long gate simultaneously
    assert row["long_joint_bars"] == 1
    assert row["long_joint_pct"] == 10.0
    assert gl.tolist() == [False, False, False, True, False, False, False, False, False, False]

    # short side per-gate pass rates
    assert row["short_price_pct"] == 100.0
    assert row["short_adv_pct"] == 50.0
    assert row["short_rrs_d1_pct"] == 30.0
    assert row["short_ha_pct"] == 20.0
    assert row["short_sma_pct"] == 40.0
    assert row["short_headroom_pct"] == 100.0
    assert row["short_volume_d1_pct"] == 100.0
    assert row["short_rrs_m5_pct"] == 50.0
    assert row["short_vwap_pct"] == 60.0

    # adv (only True rows 0-4) and rrs_d1/ha/sma-short (only True rows 6-9
    # or later) never overlap -- no bar clears every short gate at once.
    assert row["short_joint_bars"] == 0
    assert row["short_joint_pct"] == 0.0
    assert not gs.any()


def test_symbol_gate_rates_adv20_drives_result_not_df_bar_volume():
    # This is the check that would have caught the original M6 ADV-gate
    # bug: df["volume"] is a huge constant that would pass any realistic
    # min_adv_shares if (incorrectly) rolling-averaged directly, but the
    # real adv20 series fails half the bars. symbol_gate_rates's long_adv_pct
    # must reflect adv20 (50%), not a df-volume-driven 100%.
    df, feat, adv20, config = _gate_rates_fixture()
    row, _gl, _gs = symbol_gate_rates("TEST", df, feat, adv20, config)
    assert row["long_adv_pct"] == 50.0
    assert row["short_adv_pct"] == 50.0


def _watchlist_reach_feat(rolling_rrs_m5):
    n = len(rolling_rrs_m5)
    return pd.DataFrame(
        {
            "rolling_rrs_m5": rolling_rrs_m5,
            "rolling_rrs_d1": [2.0] * n,
            "ha_cont_d1": [4] * n,
            "close": [100.0] * n,
            "power_index_m5": [0.0] * n,
            "rvol_m5": [2.0] * n,
            "headroom_long": [None] * n,
            "headroom_short": [None] * n,
            "rrs_m5": [1.0] * n,
            "lrsi_m5": [50.0] * n,
        },
        index=pd.RangeIndex(n),
    )


def test_symbol_watchlist_reach_walks_through_designed_state_progression():
    # rolling_rrs_m5 crosses negative then back up through zero at bar 3,
    # arming the dip; gate_long is held True throughout so the bar
    # afterwards auto-advances DIP_ARMED -> ENTRY_EVAL -> QUALIFIED.
    # Every bar's score_long_m5 evaluates to a constant 55 given this
    # feat (well above min_list_score=50/min_hold_score=40), so "holds"
    # is true whenever gate_long is true -- state transitions are driven
    # purely by the RRS-crossing pattern below, exactly like
    # test_watchlist_m5.py's next_state_long tests.
    feat = _watchlist_reach_feat([1.0, 1.0, -0.5, 1.0, 1.0, 1.0])
    gate_long = pd.Series([True] * 6)
    gate_short = pd.Series([False] * 6)
    config = BacktestConfigM5()

    result = symbol_watchlist_reach(feat, gate_long, gate_short, config)

    assert result["long_bars_idle"] == 0
    assert result["long_bars_qualified"] == 4
    assert result["long_bars_dip_armed"] == 1
    assert result["long_bars_entry_eval"] == 1
    assert result["long_ever_reached"] == ",".join(sorted({IDLE, QUALIFIED, DIP_ARMED, ENTRY_EVAL}))

    # short gate never passes -- short book never leaves IDLE
    assert result["short_bars_idle"] == 6
    assert result["short_bars_qualified"] == 0
    assert result["short_bars_dip_armed"] == 0
    assert result["short_bars_entry_eval"] == 0
    assert result["short_ever_reached"] == IDLE


def test_symbol_watchlist_reach_never_leaves_idle_when_gate_always_false():
    feat = _watchlist_reach_feat([1.0, 1.0, 1.0])
    gate_long = pd.Series([False] * 3)
    gate_short = pd.Series([False] * 3)
    config = BacktestConfigM5()

    result = symbol_watchlist_reach(feat, gate_long, gate_short, config)

    assert result["long_bars_idle"] == 3
    assert result["long_bars_qualified"] == 0
    assert result["long_bars_dip_armed"] == 0
    assert result["long_bars_entry_eval"] == 0
    assert result["long_ever_reached"] == IDLE

    assert result["short_bars_idle"] == 3
    assert result["short_ever_reached"] == IDLE


# --- lighter end-to-end test on run_gate_pass_audit ------------------------
#
# Duplicated locally rather than imported from test_engine_m5_backtest.py,
# matching this project's existing convention of test files not sharing
# fixtures across files.


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


DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=5)]


@pytest.fixture
def small_universe():
    from rs_spy.data.resample import resample_ohlcv

    spy_m1 = _build_m1(DATES, drift=0.0005, seed=1)
    aapl_m1 = _build_m1(DATES, drift=0.0008, seed=3)
    msft_m1 = _build_m1(DATES, drift=-0.0003, seed=4)

    spy_m5 = resample_ohlcv(spy_m1, "5min")
    aapl_m5 = resample_ohlcv(aapl_m1, "5min")
    msft_m5 = resample_ohlcv(msft_m1, "5min")

    spy_d1 = _build_d1(spy_m1)
    aapl_d1 = _build_d1(aapl_m1)
    msft_d1 = _build_d1(msft_m1)

    return {
        "spy_m1": spy_m1, "spy_m5": spy_m5, "spy_d1": spy_d1,
        "aapl_m1": aapl_m1, "aapl_m5": aapl_m5, "aapl_d1": aapl_d1,
        "msft_m1": msft_m1, "msft_m5": msft_m5, "msft_d1": msft_d1,
    }


def test_run_gate_pass_audit_end_to_end_wires_together(small_universe):
    u = small_universe
    universe_m1 = {"AAPL": u["aapl_m1"], "MSFT": u["msft_m1"]}
    universe_m5 = {"AAPL": u["aapl_m5"], "MSFT": u["msft_m5"]}
    universe_d1 = {"AAPL": u["aapl_d1"], "MSFT": u["msft_d1"]}

    result = run_gate_pass_audit(
        universe_m1=universe_m1,
        universe_m5=universe_m5,
        universe_d1=universe_d1,
        spy_m1=u["spy_m1"], spy_m5=u["spy_m5"], spy_d1=u["spy_d1"],
        config=BacktestConfigM5(),
    )

    per_gate = result["per_gate"]
    watchlist_df = result["watchlist"]
    summary = result["summary"]

    assert len(per_gate) == 2
    assert set(per_gate["symbol"]) == {"AAPL", "MSFT"}
    for col in ("symbol", "n_native_bars", "long_joint_pct", "long_joint_bars",
                "short_joint_pct", "short_joint_bars"):
        assert col in per_gate.columns

    assert len(watchlist_df) == 2
    assert set(watchlist_df["symbol"]) == {"AAPL", "MSFT"}
    for col in ("long_bars_idle", "long_bars_qualified", "long_bars_dip_armed",
                "long_bars_entry_eval", "long_ever_reached",
                "short_bars_idle", "short_bars_qualified", "short_bars_dip_armed",
                "short_bars_entry_eval", "short_ever_reached"):
        assert col in watchlist_df.columns

    assert summary["n_symbols"] == 2
    for key in ("long_joint_pct_min", "long_joint_pct_mean", "long_joint_pct_median",
                "long_joint_pct_max", "short_joint_pct_min", "short_joint_pct_mean",
                "short_joint_pct_median", "short_joint_pct_max",
                "n_symbols_ever_long_dip_armed", "n_symbols_ever_long_entry_eval",
                "n_symbols_ever_short_dip_armed", "n_symbols_ever_short_entry_eval"):
        assert key in summary
