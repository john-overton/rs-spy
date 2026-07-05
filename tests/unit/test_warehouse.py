import duckdb
import pytest

from rs_spy.data.warehouse import connect


def test_connect_read_write_creates_schema(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    # bars + fetch_manifest exist and are writable
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"bars", "fetch_manifest"} <= tables
    con.execute(
        "INSERT INTO bars (symbol, timespan, ts) VALUES ('AAA', 'day', TIMESTAMP '2023-01-03')"
    )
    assert con.execute("SELECT count(*) FROM bars").fetchone()[0] == 1


def test_read_only_open_can_read_but_not_write(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    # create + seed via a read-write connection, then release it (DuckDB allows
    # only one read-write connection, so it must be closed before opening RO).
    rw = connect(path)
    rw.execute(
        "INSERT INTO bars (symbol, timespan, ts) VALUES ('AAA', 'day', TIMESTAMP '2023-01-03')"
    )
    rw.close()

    ro = connect(path, read_only=True)
    assert ro.execute("SELECT count(*) FROM bars").fetchone()[0] == 1
    with pytest.raises(duckdb.Error):
        ro.execute(
            "INSERT INTO bars (symbol, timespan, ts) VALUES ('BBB', 'day', TIMESTAMP '2023-01-04')"
        )


def test_read_only_supports_concurrent_readers(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    connect(path).close()  # create the file + schema

    # Two simultaneous read-only connections is exactly what lets multiple
    # backtest processes read the same warehouse at once.
    ro1 = connect(path, read_only=True)
    ro2 = connect(path, read_only=True)
    assert ro1.execute("SELECT count(*) FROM bars").fetchone()[0] == 0
    assert ro2.execute("SELECT count(*) FROM bars").fetchone()[0] == 0
