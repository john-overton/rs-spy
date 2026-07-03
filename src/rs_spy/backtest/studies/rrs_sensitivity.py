"""M3.5 RRS parameter sensitivity sweep.
algo-spec/08-backtesting-and-validation.md §3.3, D1-adapted.

Sweeps the RRS rolling window, the gate qualification threshold, and
rolling-vs-raw RRS as the gate basis, re-running the full D1 backtest for
each combination and collecting the 08 §2 primary metrics. Window values
{3, 5, 8} are the D1-cadence analog of the spec's M5-bar {6, 12, 18} sweep,
scaled around the M3 default of 5.

Spec's expectation: the edge should be broad and stable across the sweep --
a sharp peak at one setting is a red flag for overfitting, not evidence of
a well-tuned system.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine import BacktestConfig, run_d1_backtest
from rs_spy.backtest.metrics import compute_metrics

WINDOWS = (3, 5, 8)
THRESHOLDS = (0.75, 1.0, 1.5)
BASES = ("rolling", "raw")


def run_rrs_sensitivity(
    bars_by_symbol: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    sectors: dict[str, str],
    earnings_blackout: dict[str, set] | None = None,
    base_config: BacktestConfig | None = None,
) -> pd.DataFrame:
    base_config = base_config or BacktestConfig()
    earnings_blackout = earnings_blackout or {}

    rows = []
    for window in WINDOWS:
        for threshold in THRESHOLDS:
            for basis in BASES:
                cfg = replace(
                    base_config,
                    rrs_window=window,
                    rrs_threshold_long=threshold,
                    rrs_threshold_short=-threshold,
                    rrs_use_rolling=(basis == "rolling"),
                )
                result = run_d1_backtest(bars_by_symbol, spy, qqq, sectors, earnings_blackout, cfg)
                trades = result.trades_df()
                trading_days = len(result.equity_curve) if result.equity_curve is not None else 0
                metrics = compute_metrics(trades, result.equity_curve, trading_days)
                rows.append({"window": window, "threshold": threshold, "basis": basis, **metrics})

    return pd.DataFrame(rows)
