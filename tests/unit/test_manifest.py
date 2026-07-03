from rs_spy.data import manifest
from rs_spy.data.warehouse import connect


def test_pending_symbols_all_pending_initially(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    pending = manifest.pending_symbols(con, ["AAA", "BBB"], "day", "2023")
    assert pending == ["AAA", "BBB"]


def test_pending_symbols_excludes_ok_and_empty(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    manifest.record(con, "AAA", "day", "2023", "ok", row_count=5)
    manifest.record(con, "BBB", "day", "2023", "empty", row_count=0)
    pending = manifest.pending_symbols(con, ["AAA", "BBB", "CCC"], "day", "2023")
    assert pending == ["CCC"]


def test_pending_symbols_includes_error_status(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    manifest.record(con, "AAA", "day", "2023", "error", error="boom")
    pending = manifest.pending_symbols(con, ["AAA"], "day", "2023")
    assert pending == ["AAA"]


def test_record_upserts_on_conflict(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    manifest.record(con, "AAA", "day", "2023", "error", error="boom")
    manifest.record(con, "AAA", "day", "2023", "ok", row_count=10)
    row = con.execute(
        "SELECT status, row_count FROM fetch_manifest WHERE symbol='AAA' AND unit_key='2023'"
    ).fetchone()
    assert row == ("ok", 10)
