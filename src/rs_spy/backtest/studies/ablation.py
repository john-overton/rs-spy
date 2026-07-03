"""M3.5 rule-count ablation study. algo-spec/08-backtesting-and-validation.md
§3.1 ("Keeping it Really Simple"), D1-adapted.

Re-runs the D1 backtest with each of the four D1-analog hard rules (market
bias, RRS -- standing in for the spec's VWAP rule per selection/gates.py's
own documented substitution, HA continuation, SMA stack) individually
disabled, then scores every resulting trade (baseline + each single-disable
run, deduped by symbol/entry_date) by how many of the four rules it
actually satisfied on its entry signal date -- independent of which run
produced it, so a trade let through by a disabled gate is still scored
against the full, always-on yardstick. Buckets trades by that count and
reports win rate / expectancy per bucket.

Spec's expectation: both increase monotonically with rules satisfied; a
gate that doesn't improve results when present is suspect and should be
investigated before being deleted.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine import BacktestConfig, _align_calendar, _prepare, run_d1_backtest
from rs_spy.bias.engine_d1 import BULL, STRONG_BULL, bias_series_d1
from rs_spy.selection import gates

HARD_RULES = ("bias", "rrs", "ha", "sma")


def _rule_satisfaction(bars_by_symbol, spy, qqq, base_config, trades) -> pd.DataFrame:
    """Score each (LONG-only) trade's entry signal day against the 4 hard
    rules using the fully-enabled pipeline, regardless of which ablation
    run actually produced the trade."""
    calendar = _align_calendar(spy, bars_by_symbol)
    spy_p, qqq_p, features, *_ = _prepare(
        calendar, spy, qqq, bars_by_symbol, rrs_window=base_config.rrs_window
    )
    bias_df = bias_series_d1(spy_p, qqq_p)

    rows = []
    for t in trades:
        if t.direction != "LONG" or t.symbol not in features or t.entry_date not in calendar:
            continue
        idx = calendar.get_loc(t.entry_date)
        signal_idx = idx - 1
        if signal_idx < 0:
            continue
        feat = features[t.symbol]
        bias_ok = bias_df["bias"].iat[signal_idx] in (BULL, STRONG_BULL)
        rrs_ok = bool(gates.gate_rrs_long(feat).iat[signal_idx])
        ha_ok = bool(gates.gate_ha_long(feat).iat[signal_idx])
        sma_ok = bool(gates.gate_sma_long(feat).iat[signal_idx])
        rows.append(
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date,
                "signal_date": calendar[signal_idx],
                "pnl": t.pnl,
                "r_multiple": t.r_multiple,
                "bias_ok": bias_ok,
                "rrs_ok": rrs_ok,
                "ha_ok": ha_ok,
                "sma_ok": sma_ok,
                "rule_count": sum([bias_ok, rrs_ok, ha_ok, sma_ok]),
            }
        )
    return pd.DataFrame(rows)


def run_gate_ablation(
    bars_by_symbol: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    sectors: dict[str, str],
    earnings_blackout: dict[str, set] | None = None,
    base_config: BacktestConfig | None = None,
) -> dict[str, pd.DataFrame]:
    base_config = base_config or BacktestConfig()
    earnings_blackout = earnings_blackout or {}

    runs = {"baseline": base_config}
    for rule in HARD_RULES:
        runs[f"disable_{rule}"] = replace(
            base_config, disabled_gates=frozenset(base_config.disabled_gates | {rule})
        )

    all_trades = []
    seen: set[tuple] = set()
    run_trade_counts = {}
    for label, cfg in runs.items():
        result = run_d1_backtest(bars_by_symbol, spy, qqq, sectors, earnings_blackout, cfg)
        run_trade_counts[label] = len(result.trades)
        for t in result.trades:
            key = (t.symbol, t.entry_date, t.direction)
            if key in seen:
                continue
            seen.add(key)
            all_trades.append(t)

    scored = _rule_satisfaction(bars_by_symbol, spy, qqq, base_config, all_trades)
    if scored.empty:
        return {"trades": scored, "summary": pd.DataFrame(), "run_trade_counts": run_trade_counts}

    summary = (
        scored.groupby("rule_count")
        .agg(
            n_trades=("pnl", "size"),
            win_rate=("pnl", lambda s: (s > 0).mean()),
            avg_r=("r_multiple", "mean"),
            expectancy=("pnl", "mean"),
        )
        .reindex(range(5))
        .reset_index()
    )

    return {"trades": scored, "summary": summary, "run_trade_counts": run_trade_counts}
