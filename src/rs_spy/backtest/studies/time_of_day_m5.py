"""M7 time-of-day / regime slicing. algo-spec/08-backtesting-and-validation.md
§3.5. Not yet built at any cadence.

Slices a real M5 backtest's realized trades by (a) entry time-of-day bucket
(OPEN 09:30-10:30 ET, MIDDAY 10:30-14:30 ET, CLOSE 14:30-15:55 ET -- the
session structure algo-spec 05/06/07 reference throughout) and (b) the D1
regime (bias/regime.py's TREND_UP/CHOP/TREND_DOWN) in effect at the entry
bar, reporting trade count / win rate / expectancy per bucket, separately
by direction (algo-spec 08 §3's "long and short reported separately").
Needs no additional backtest run -- takes an already-computed trade log and
regime series from the caller's own baseline run.
"""
import pandas as pd

OPEN = "OPEN"
MIDDAY = "MIDDAY"
CLOSE = "CLOSE"

_OPEN_END = pd.Timedelta(hours=10, minutes=30)
_MIDDAY_END = pd.Timedelta(hours=14, minutes=30)

SUMMARY_COLUMNS = ["direction", "time_of_day", "regime", "n_trades", "win_rate", "expectancy", "total_pnl"]


def _time_of_day_bucket(entry_time: pd.Timestamp) -> str:
    et = entry_time.tz_convert("America/New_York")
    tod = pd.Timedelta(hours=et.hour, minutes=et.minute, seconds=et.second)
    if tod < _OPEN_END:
        return OPEN
    if tod < _MIDDAY_END:
        return MIDDAY
    return CLOSE


def run_time_of_day_regime_slice_m5(trades: pd.DataFrame, regime_d1_m5: pd.Series) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    df = trades.copy()
    df["time_of_day"] = df["entry_time"].apply(_time_of_day_bucket)
    df["regime"] = df["entry_time"].apply(lambda t: regime_d1_m5.asof(t))

    return (
        df.groupby(["direction", "time_of_day", "regime"])
        .agg(n_trades=("pnl", "size"), win_rate=("pnl", lambda s: (s > 0).mean()),
             expectancy=("pnl", "mean"), total_pnl=("pnl", "sum"))
        .reset_index()
    )
