"""Load cached bars from the DuckDB warehouse into per-symbol DataFrames
indexed by trading date, for indicator/backtest consumption."""
import duckdb
import pandas as pd

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
