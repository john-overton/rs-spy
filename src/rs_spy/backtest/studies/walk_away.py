"""M3.5 walk-away analysis. algo-spec/08-backtesting-and-validation.md §3.2
(the wiki's diagnostic), D1-adapted.

For every day a symbol first qualifies for the long watchlist (an IDLE ->
QUALIFIED transition -- an "entry signal" whether or not it was ultimately
traded, including ones skipped by position/sector caps), records the
maximum favorable and adverse excursion (MFE/MAE) over the following
`horizon_days`, expressed in the same R units as backtest/engine.py's
realized `r_multiple` (price move / (STOP_ATR_MULT * entry-day ATR)), had a
position been entered at the next day's open and simply held with no
active management. Comparing this "walk away and do nothing" distribution
against the realized trades' r_multiple distribution from an actual
backtest run indicates how much of the system's P&L is determined by exit
rules vs. stock picks.
"""
import pandas as pd

from rs_spy.backtest.engine import STOP_ATR_MULT, BacktestConfig, _align_calendar, _prepare, run_d1_backtest
from rs_spy.selection import gates, watchlist


def _entry_signals(bars_by_symbol, spy, qqq, earnings_blackout, config):
    calendar = _align_calendar(spy, bars_by_symbol)
    spy_p, qqq_p, features, scores_long, _scores_short, _ema8 = _prepare(
        calendar, spy, qqq, bars_by_symbol, rrs_window=config.rrs_window
    )
    rrs_column = "rolling_rrs_d1" if config.rrs_use_rolling else "rrs_d1"

    gate_long = {}
    for sym, df in bars_by_symbol.items():
        df = df.loc[calendar]
        gate_long[sym] = gates.gates_pass_long(
            df,
            features[sym],
            earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_threshold=config.rrs_threshold_long,
            rrs_column=rrs_column,
            disabled=config.disabled_gates,
        )

    signals = []
    state = dict.fromkeys(bars_by_symbol, watchlist.IDLE)
    for i, day in enumerate(calendar):
        for sym in bars_by_symbol:
            gp = bool(gate_long[sym].iat[i]) if not pd.isna(gate_long[sym].iat[i]) else False
            score = scores_long[sym].iat[i]
            rrs_now = features[sym]["rrs_d1"].iat[i]
            rrs_prev = features[sym]["rrs_d1"].iat[i - 1] if i > 0 else None
            prev_state = state[sym]
            new_state = watchlist.next_state_long(
                prev_state,
                gp,
                score,
                rrs_prev,
                rrs_now,
                min_list_score=config.min_list_score,
                min_hold_score=config.min_hold_score,
            )
            if prev_state == watchlist.IDLE and new_state == watchlist.QUALIFIED:
                signals.append((sym, i, day))
            state[sym] = new_state
    return calendar, features, signals


def run_walk_away(
    bars_by_symbol: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    sectors: dict[str, str],
    earnings_blackout: dict[str, set] | None = None,
    config: BacktestConfig | None = None,
    horizon_days: int = 20,
) -> dict[str, pd.DataFrame]:
    config = config or BacktestConfig()
    earnings_blackout = earnings_blackout or {}
    calendar, features, signals = _entry_signals(bars_by_symbol, spy, qqq, earnings_blackout, config)

    rows = []
    for sym, i, day in signals:
        entry_idx = i + 1
        if entry_idx >= len(calendar):
            continue
        atr = features[sym]["atr_d1"].iat[i]
        if pd.isna(atr) or atr <= 0:
            continue
        df = bars_by_symbol[sym].loc[calendar]
        entry_price = df["open"].iat[entry_idx]
        r_basis = STOP_ATR_MULT * atr
        end_idx = min(entry_idx + horizon_days, len(calendar) - 1)
        window = df.iloc[entry_idx : end_idx + 1]
        if window.empty:
            continue
        rows.append(
            {
                "symbol": sym,
                "signal_date": day,
                "entry_date": calendar[entry_idx],
                "entry_price": entry_price,
                "mfe_r": (window["high"].max() - entry_price) / r_basis,
                "mae_r": (window["low"].min() - entry_price) / r_basis,
                "horizon_bars": len(window) - 1,
            }
        )
    signals_df = pd.DataFrame(rows)

    result = run_d1_backtest(bars_by_symbol, spy, qqq, sectors, earnings_blackout, config)
    realized = result.trades_df()

    return {"signals": signals_df, "realized_trades": realized}
