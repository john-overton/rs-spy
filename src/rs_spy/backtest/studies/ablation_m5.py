"""M7 rule-count ablation study. algo-spec/08-backtesting-and-validation.md
§3.1 ("Keeping it Really Simple"), M5-adapted from M3.5's D1-cadence version
(ablation.py). Extends the D1 4-hard-rule set to the full M5 6-hard-rule set
(selection/gates.py's HARD_RULE_NAMES minus the D1-inapplicable subset):
market bias, RRS (D1), Heikin-Ashi continuation, SMA stack, RRS (M5), VWAP.

Re-runs the M5 backtest with each hard rule individually disabled (plus the
caller-supplied baseline run, to avoid a redundant extra full-universe
precompute -- see this module's docstring on `baseline_prepared`/
`baseline_result`). Every resulting trade (deduped by symbol/entry_time/
direction across all runs) is scored against the FULL, always-on 6-rule
yardstick at its own entry SIGNAL bar (one bar before the fill, matching
broker_sim.py's next-bar-fill convention -- the same fixed 1-bar-lag
approximation ablation.py's D1 precedent uses for D1's 1-day lag),
independent of which run produced it. Trades are bucketed by how many of
the 6 rules they satisfied and win rate/expectancy reported per bucket,
separately for LONG and SHORT (algo-spec 08 §3's "long and short reported
separately").

Spec's expectation: both should increase monotonically with rules
satisfied; a rule that doesn't improve results when present is suspect.
M3.5's D1 version (4 rules, 8 trades) found this uninformative -- no
ablated rule ever unlocked a new trade. Worth checking whether the fuller
M5 rule set, or a larger real trade count, behaves differently.
"""
from dataclasses import replace

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5, run_m5_backtest
from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL
from rs_spy.selection import gates

HARD_RULES_M5 = ("bias", "rrs", "ha", "sma", "rrs_m5", "vwap")


def _rule_ok_long(prepared, sym: str, i: int) -> dict:
    feat = prepared.features[sym]
    return {
        "bias_ok": prepared.bias_df["bias"].iat[i] in (BULL, STRONG_BULL),
        "rrs_ok": bool(gates.gate_rrs_long(feat).iat[i]),
        "ha_ok": bool(gates.gate_ha_long(feat).iat[i]),
        "sma_ok": bool(gates.gate_sma_long(feat).iat[i]),
        "rrs_m5_ok": bool(gates.gate_rrs_m5_long(feat).iat[i]),
        "vwap_ok": bool(gates.gate_vwap_long(feat).iat[i]),
    }


def _rule_ok_short(prepared, sym: str, i: int) -> dict:
    feat = prepared.features[sym]
    return {
        "bias_ok": prepared.bias_df["bias"].iat[i] in (BEAR, STRONG_BEAR),
        "rrs_ok": bool(gates.gate_rrs_short(feat).iat[i]),
        "ha_ok": bool(gates.gate_ha_short(feat).iat[i]),
        "sma_ok": bool(gates.gate_sma_short(feat).iat[i]),
        "rrs_m5_ok": bool(gates.gate_rrs_m5_short(feat).iat[i]),
        "vwap_ok": bool(gates.gate_vwap_short(feat).iat[i]),
    }


def _score_trades(prepared, trades) -> pd.DataFrame:
    """Score each trade's entry SIGNAL bar (one bar before `entry_time`)
    against the full, always-on 6-rule yardstick, using `prepared`'s
    features/bias -- independent of which ablation run actually produced
    the trade (a trade let through by a disabled gate is still scored
    against the full rule set)."""
    calendar = prepared.calendar
    rows = []
    for t in trades:
        if t.symbol not in prepared.features or t.entry_time not in calendar:
            continue
        entry_idx = calendar.get_loc(t.entry_time)
        signal_idx = entry_idx - 1
        if signal_idx < 0:
            continue
        checks = (
            _rule_ok_long(prepared, t.symbol, signal_idx) if t.direction == "LONG"
            else _rule_ok_short(prepared, t.symbol, signal_idx)
        )
        rows.append({
            "symbol": t.symbol, "direction": t.direction,
            "entry_time": t.entry_time, "signal_time": calendar[signal_idx],
            "pnl": t.pnl, "r_multiple": t.r_multiple,
            **checks,
            "rule_count": sum(checks.values()),
        })
    return pd.DataFrame(rows)


def run_gate_ablation_m5(
    universe_m1: dict, universe_m5: dict, universe_d1: dict,
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame, qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None,
    base_config: BacktestConfigM5,
    baseline_prepared,
    baseline_result,
) -> dict:
    earnings_blackout = earnings_blackout or {}

    all_trades = list(baseline_result.trades)
    seen = {(t.symbol, t.entry_time, t.direction) for t in all_trades}
    run_trade_counts = {"baseline": len(baseline_result.trades)}

    for rule in HARD_RULES_M5:
        cfg = replace(base_config, disabled_gates=frozenset(base_config.disabled_gates | {rule}))
        result = run_m5_backtest(
            universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
            sectors, earnings_blackout, cfg,
        )
        run_trade_counts[f"disable_{rule}"] = len(result.trades)
        for t in result.trades:
            key = (t.symbol, t.entry_time, t.direction)
            if key in seen:
                continue
            seen.add(key)
            all_trades.append(t)

    scored = _score_trades(baseline_prepared, all_trades)
    if scored.empty:
        return {
            "trades": scored, "summary_long": pd.DataFrame(), "summary_short": pd.DataFrame(),
            "run_trade_counts": run_trade_counts,
        }

    summaries = {}
    for direction in ("LONG", "SHORT"):
        sub = scored[scored["direction"] == direction]
        if sub.empty:
            summaries[direction] = pd.DataFrame(columns=["rule_count", "n_trades", "win_rate", "avg_r", "expectancy"])
            continue
        summaries[direction] = (
            sub.groupby("rule_count")
            .agg(n_trades=("pnl", "size"), win_rate=("pnl", lambda s: (s > 0).mean()),
                 avg_r=("r_multiple", "mean"), expectancy=("pnl", "mean"))
            .reindex(range(len(HARD_RULES_M5) + 1))
            .reset_index()
        )

    return {
        "trades": scored, "summary_long": summaries["LONG"], "summary_short": summaries["SHORT"],
        "run_trade_counts": run_trade_counts,
    }
