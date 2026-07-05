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


def test_symbols_with_error_units_finds_partial_holes(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    manifest.record(con, "AAA", "minute", "2024-03", "ok", row_count=100)
    manifest.record(con, "AAA", "minute", "2024-04", "error", error="boom")
    manifest.record(con, "BBB", "minute", "2024-03", "ok", row_count=100)
    manifest.record(con, "CCC", "day", "2024", "error", error="boom")
    out = manifest.symbols_with_error_units(con, ["AAA", "BBB", "CCC"])
    assert out == ["AAA", "CCC"]  # BBB has no error units at all


def test_symbols_with_error_units_respects_timespan_filter(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    manifest.record(con, "AAA", "day", "2024", "error", error="boom")
    assert manifest.symbols_with_error_units(con, ["AAA"], timespans=("minute",)) == []
    assert manifest.symbols_with_error_units(con, ["AAA"], timespans=("day",)) == ["AAA"]


def test_symbols_with_error_units_empty_symbols_list(tmp_path):
    con = connect(tmp_path / "warehouse.duckdb")
    assert manifest.symbols_with_error_units(con, []) == []
