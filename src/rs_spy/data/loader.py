"""Load cached bars from the DuckDB warehouse into per-symbol DataFrames
indexed by trading date, for indicator/backtest consumption."""
import duckdb
import pandas as pd

from rs_spy.data.resample import resample_ohlcv
from rs_spy.data.session import filter_rth


def load_daily_bars(con: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    df = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE symbol = ? AND timespan = 'day' ORDER BY ts",
        [symbol],
    ).df()
    df["ts"] = pd.to_datetime(df["ts"]).dt.normalize()
    return df.set_index("ts")


def load_universe_daily_bars(
    con: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, pd.DataFrame]:
    return {sym: load_daily_bars(con, sym) for sym in symbols}


def load_minute_bars(con: duckdb.DuckDBPyConnection, symbol: str, rth_only: bool = True) -> pd.DataFrame:
    """`ts` is the true UTC instant (see data/warehouse.py). RTH-filtered by
    default -- see data/session.py for why that matters (the feed includes
    pre/post-market bars that session-anchored indicators like VWAP/RVOL
    must not see)."""
    df = con.execute(
        "SELECT ts, open, high, low, close, volume, vwap, trade_count FROM bars "
        "WHERE symbol = ? AND timespan = 'minute' ORDER BY ts",
        [symbol],
    ).df()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    return filter_rth(df) if rth_only else df


def load_universe_minute_bars(
    con: duckdb.DuckDBPyConnection, symbols: list[str], rth_only: bool = True
) -> dict[str, pd.DataFrame]:
    return {sym: load_minute_bars(con, sym, rth_only=rth_only) for sym in symbols}


def load_universe_m1_bars(
    con: duckdb.DuckDBPyConnection, symbols: list[str], rth_only: bool = True
) -> dict[str, pd.DataFrame]:
    """Alias of load_universe_minute_bars -- named to match load_universe_m5_bars'
    "m1"/"m5" naming for callers (backtest/engine_m5.py) that need both cadences
    side by side and read more clearly with parallel names."""
    return load_universe_minute_bars(con, symbols, rth_only=rth_only)


def load_m5_bars(con: duckdb.DuckDBPyConnection, symbol: str, rth_only: bool = True) -> pd.DataFrame:
    """True 5-minute bars, built by resampling the warehouse's raw 1-minute
    bars (see data/resample.py for why this step exists -- the spec's "M5"
    indicators are calibrated for 5-minute spacing, not the 1-minute bars
    Alpaca actually returns)."""
    m1 = load_minute_bars(con, symbol, rth_only=rth_only)
    return resample_ohlcv(m1, "5min")


def load_universe_m5_bars(
    con: duckdb.DuckDBPyConnection, symbols: list[str], rth_only: bool = True
) -> dict[str, pd.DataFrame]:
    return {sym: load_m5_bars(con, sym, rth_only=rth_only) for sym in symbols}
