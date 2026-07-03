"""M3.5 validation studies: gate-disable plumbing + end-to-end smoke tests
against a small synthetic universe. Not asserting economic conclusions
(the studies are diagnostics run against real data, not a spec-conformance
surface) -- just that ablation/walk-away/RRS-sweep wiring produces
correctly-shaped, exception-free output and that `disabled` actually
bypasses the gate it names.
"""
import numpy as np
import pandas as pd

from rs_spy.backtest.engine import BacktestConfig
from rs_spy.backtest.studies.ablation import run_gate_ablation
from rs_spy.backtest.studies.rrs_sensitivity import run_rrs_sensitivity
from rs_spy.backtest.studies.walk_away import run_walk_away
from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL
from rs_spy.selection import gates


# gate_adv's 20-bar rolling mean needs >=20 rows of history before it's
# non-NaN; only the last row's values matter for these gate checks.
_N_ROWS = 20


def _features_row(rrs_ok: bool, ha_ok: bool, sma_ok: bool) -> pd.DataFrame:
    idx = pd.RangeIndex(_N_ROWS)
    return pd.DataFrame(
        {
            "rolling_rrs_d1": [2.0 if rrs_ok else -2.0] * _N_ROWS,
            "rrs_d1": [2.0 if rrs_ok else -2.0] * _N_ROWS,
            "ha_cont_d1": [3 if ha_ok else -3] * _N_ROWS,
            "sma_stack": [ABOVE_ALL if sma_ok else BELOW_ALL] * _N_ROWS,
            "headroom_long": [np.nan] * _N_ROWS,
            "volume_ratio_d1": [10.0] * _N_ROWS,
        },
        index=idx,
    )


def test_gates_pass_long_disabled_rrs_bypasses_rrs_gate():
    df = pd.DataFrame({"close": [50.0] * _N_ROWS, "volume": [2_000_000.0] * _N_ROWS})
    feat = _features_row(rrs_ok=False, ha_ok=True, sma_ok=True)

    assert not gates.gates_pass_long(df, feat, min_adv_shares=1.0).iloc[-1]
    assert gates.gates_pass_long(df, feat, min_adv_shares=1.0, disabled=frozenset({"rrs"})).iloc[-1]


def test_gates_pass_long_disabled_sma_bypasses_sma_gate():
    df = pd.DataFrame({"close": [50.0] * _N_ROWS, "volume": [2_000_000.0] * _N_ROWS})
    feat = _features_row(rrs_ok=True, ha_ok=True, sma_ok=False)

    assert not gates.gates_pass_long(df, feat, min_adv_shares=1.0).iloc[-1]
    assert gates.gates_pass_long(df, feat, min_adv_shares=1.0, disabled=frozenset({"sma"})).iloc[-1]


def test_gate_rrs_long_respects_column_override():
    feat = pd.DataFrame({"rolling_rrs_d1": [2.0], "rrs_d1": [-2.0]})
    assert gates.gate_rrs_long(feat, threshold=1.0).iloc[0]
    assert not gates.gate_rrs_long(feat, threshold=1.0, column="rrs_d1").iloc[0]


def _synthetic_universe(n=120, n_symbols=3, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)

    def make(seed_i, drift):
        r = np.random.default_rng(seed_i)
        close = 100 + np.cumsum(r.normal(drift, 1.0, n))
        open_ = close - r.normal(0, 0.3, n)
        high = np.maximum(open_, close) + np.abs(r.normal(0, 0.5, n))
        low = np.minimum(open_, close) - np.abs(r.normal(0, 0.5, n))
        volume = r.integers(2_000_000, 5_000_000, n).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )

    spy = make(1, 0.05)
    qqq = make(2, 0.05)
    bars = {f"SYM{i}": make(100 + i, rng.normal(0.03, 0.05)) for i in range(n_symbols)}
    sectors = {sym: "Technology" for sym in bars}
    return bars, spy, qqq, sectors


def test_run_gate_ablation_smoke():
    bars, spy, qqq, sectors = _synthetic_universe()
    config = BacktestConfig(min_adv_shares=1.0)
    result = run_gate_ablation(bars, spy, qqq, sectors, base_config=config)

    assert set(result["run_trade_counts"]) == {
        "baseline", "disable_bias", "disable_rrs", "disable_ha", "disable_sma"
    }
    if not result["trades"].empty:
        assert {"rule_count", "pnl", "r_multiple"}.issubset(result["trades"].columns)
        assert result["trades"]["rule_count"].between(0, 4).all()


def test_run_walk_away_smoke():
    bars, spy, qqq, sectors = _synthetic_universe()
    config = BacktestConfig(min_adv_shares=1.0)
    result = run_walk_away(bars, spy, qqq, sectors, config=config, horizon_days=10)

    assert "signals" in result and "realized_trades" in result
    if not result["signals"].empty:
        assert {"mfe_r", "mae_r", "horizon_bars"}.issubset(result["signals"].columns)
        assert (result["signals"]["mfe_r"] >= result["signals"]["mae_r"]).all()


def test_run_rrs_sensitivity_smoke():
    bars, spy, qqq, sectors = _synthetic_universe()
    config = BacktestConfig(min_adv_shares=1.0)
    grid = run_rrs_sensitivity(bars, spy, qqq, sectors, base_config=config)

    from rs_spy.backtest.studies.rrs_sensitivity import BASES, THRESHOLDS, WINDOWS

    assert len(grid) == len(WINDOWS) * len(THRESHOLDS) * len(BASES)
    assert {"window", "threshold", "basis", "n_trades", "win_rate"}.issubset(grid.columns)
