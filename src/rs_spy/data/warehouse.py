from pathlib import Path

import duckdb

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol       VARCHAR NOT NULL,
    timespan     VARCHAR NOT NULL,
    ts           TIMESTAMP NOT NULL,
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    volume       BIGINT,
    vwap         DOUBLE,
    trade_count  BIGINT,
    PRIMARY KEY (symbol, timespan, ts)
);

CREATE TABLE IF NOT EXISTS fetch_manifest (
    symbol       VARCHAR NOT NULL,
    timespan     VARCHAR NOT NULL,
    unit_key     VARCHAR NOT NULL,
    status       VARCHAR NOT NULL,
    row_count    INTEGER,
    error        VARCHAR,
    fetched_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, timespan, unit_key)
);
"""


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    # DuckDB's session TimeZone defaults to the OS timezone and is used to
    # convert tz-aware pandas columns to `TIMESTAMP` (naive) on insert --
    # without this, a tz-aware UTC "ts" value is silently shifted to local
    # wall-clock time and then stripped of its tz label, corrupting both the
    # date and time of every bar (discovered via a backtest trade dated on a
    # Sunday, which is impossible for real trading data). Forcing UTC makes
    # that conversion a no-op so the naive TIMESTAMP column holds true UTC.
    con.execute("SET TimeZone='UTC'")
    con.execute(_SCHEMA)
    return con
