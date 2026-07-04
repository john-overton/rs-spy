"""M7 RRS parameter sensitivity sweep. algo-spec/08-backtesting-and-validation.md
§3.3, M5-adapted from M3.5's D1-cadence version (rrs_sensitivity.py).

Sweeps RRS_M5_WINDOW (algo-spec 02/04's own {6, 12, 18} sweep for L) and the
M5 RRS gate qualification threshold, re-running the full M5 backtest for
each of the 9 combinations and collecting 08 §2 primary metrics, reported
both overall and separately by direction (algo-spec 08 §3's "long and short
reported separately").

Spec's expectation: the edge should be broad and stable across the sweep --
a sharp peak at one setting is a red flag for overfitting, not evidence of
good tuning. M3.5's D1 version found window=3 outperforming the M3 default
of 5 on every swept threshold/basis (IMPLEMENTATION.md known limitation
#6) -- worth knowing whether the M5 window (currently 12, the spec's L
default) shows a similar miscalibration.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.backtest.metrics import compute_metrics, metrics_by_direction

WINDOWS = (6, 12, 18)
THRESHOLDS = (0.75, 1.0, 1.5)


def run_rrs_sensitivity_m5(
    universe_m1: dict, universe_m5: dict, universe_d1: dict,
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None = None,
    base_config: BacktestConfigM5 | None = None,
) -> pd.DataFrame:
    base_config = base_config or BacktestConfigM5(shorts_enabled=True)
    earnings_blackout = earnings_blackout or {}

    rows = []
    for window in WINDOWS:
        for threshold in THRESHOLDS:
            cfg = replace(
                base_config,
                rrs_m5_window=window,
                rrs_m5_threshold_long=threshold,
                rrs_m5_threshold_short=-threshold,
            )
            result = run_m5_backtest(
                universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
                sectors, earnings_blackout, cfg,
            )
            trades = result.trades_df()
            trading_days = len(result.equity_curve) if result.equity_curve is not None else 0
            overall = compute_metrics(trades, result.equity_curve, trading_days)
            by_dir = metrics_by_direction(trades, base_config.starting_equity) if not trades.empty else {}

            row = {"window": window, "threshold": threshold}
            row.update({f"overall_{k}": v for k, v in overall.items()})
            for direction in ("LONG", "SHORT"):
                dm = by_dir.get(direction, {"n_trades": 0, "win_rate": None, "profit_factor": None, "total_pnl": 0.0})
                row.update({f"{direction.lower()}_{k}": v for k, v in dm.items()})
            rows.append(row)

    return pd.DataFrame(rows)
