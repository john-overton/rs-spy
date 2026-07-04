"""M7 walk-away analysis. algo-spec/08-backtesting-and-validation.md §3.2,
M5-adapted from M3.5's D1-cadence version (walk_away.py) -- see that module
for the full method description; identical idea, M5 cadence and both
directions.

For every M5 bar where a symbol's watchlist state transitions IDLE ->
QUALIFIED (an "entry signal," independent of whether a real trade later
took the "own dip" DIP_ARMED path or the 04 §6 trigger-bypass exception --
QUALIFIED is upstream of both, so this definition is unaffected by which
path a real trade eventually used), records the maximum favorable/adverse
excursion (MFE/MAE) over the following `horizon_bars` M5 bars, expressed in
the same R units as engine_m5.py's realized r_multiple (price move /
(risk.STOP_ATR_MULT * entry-bar ATR)), had a position been entered at the
NEXT bar's open and simply held with no active management. Comparing this
"walk away and do nothing" distribution against the realized trades'
r_multiple distribution indicates how much of the system's P&L is
determined by exit rules vs. stock/timing picks.
"""
import pandas as pd

from rs_spy.algo import risk
from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.selection import watchlist

DEFAULT_HORIZON_BARS = 78  # ~1 RTH session at M5 cadence (390 min / 5)


def _entry_signals_m5(prepared, direction: str, config: BacktestConfigM5) -> list[tuple]:
    gate = prepared.gate_long if direction == "LONG" else prepared.gate_short
    score = prepared.score_long if direction == "LONG" else prepared.score_short
    next_state_fn = watchlist.next_state_long if direction == "LONG" else watchlist.next_state_short
    n_bars = len(prepared.calendar)

    signals = []
    for sym in gate:
        rrs = prepared.features[sym]["rolling_rrs_m5"]
        lrsi = prepared.features[sym]["lrsi_m5"]
        state = watchlist.IDLE
        for i in range(n_bars):
            gp = bool(gate[sym].iat[i]) if not pd.isna(gate[sym].iat[i]) else False
            sc = score[sym].iat[i]
            rrs_prev = rrs.iat[i - 1] if i > 0 else None
            lrsi_prev = lrsi.iat[i - 1] if i > 0 else None
            new_state = next_state_fn(
                state, gp, sc, rrs_prev, rrs.iat[i],
                lrsi_prev=lrsi_prev, lrsi_now=lrsi.iat[i],
                min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
            )
            if state == watchlist.IDLE and new_state == watchlist.QUALIFIED:
                signals.append((sym, i))
            state = new_state
    return signals


def _walk_away_rows(prepared, direction: str, signals: list[tuple], horizon_bars: int) -> pd.DataFrame:
    calendar = prepared.calendar
    n_bars = len(calendar)
    rows = []
    for sym, i in signals:
        entry_idx = i + 1
        if entry_idx >= n_bars:
            continue
        atr = prepared.atr_m5[sym].iat[i]
        if pd.isna(atr) or atr <= 0:
            continue
        bars = prepared.bars[sym]
        entry_price = bars["open"].iat[entry_idx]
        if pd.isna(entry_price):
            continue
        r_basis = risk.STOP_ATR_MULT * atr
        end_idx = min(entry_idx + horizon_bars, n_bars - 1)
        window = bars.iloc[entry_idx : end_idx + 1]
        if window.empty or window["high"].isna().all():
            continue
        if direction == "LONG":
            mfe_r = (window["high"].max() - entry_price) / r_basis
            mae_r = (window["low"].min() - entry_price) / r_basis
        else:
            mfe_r = (entry_price - window["low"].min()) / r_basis
            mae_r = (entry_price - window["high"].max()) / r_basis
        rows.append({
            "symbol": sym, "direction": direction,
            "signal_time": calendar[i], "entry_time": calendar[entry_idx],
            "entry_price": entry_price, "mfe_r": mfe_r, "mae_r": mae_r,
            "horizon_bars": len(window) - 1,
        })
    return pd.DataFrame(rows)


def run_walk_away_m5(
    prepared, realized_trades: pd.DataFrame, config: BacktestConfigM5,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
) -> dict:
    long_signals = _entry_signals_m5(prepared, "LONG", config)
    short_signals = _entry_signals_m5(prepared, "SHORT", config)
    signals_df = pd.concat(
        [
            _walk_away_rows(prepared, "LONG", long_signals, horizon_bars),
            _walk_away_rows(prepared, "SHORT", short_signals, horizon_bars),
        ],
        ignore_index=True,
    )
    return {"signals": signals_df, "realized_trades": realized_trades}
