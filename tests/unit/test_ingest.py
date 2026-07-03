from datetime import datetime, timezone

from rs_spy.data.ingest import _batches, month_chunks, year_chunks


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_year_chunks_single_year():
    chunks = year_chunks(_dt(2023, 3, 1), _dt(2023, 9, 1))
    assert len(chunks) == 1
    assert chunks[0].year == 2023
    assert chunks[0].start == _dt(2023, 3, 1)
    assert chunks[0].end == _dt(2023, 9, 1)


def test_year_chunks_spans_multiple_years():
    chunks = year_chunks(_dt(2021, 6, 15), _dt(2024, 3, 10))
    assert [c.year for c in chunks] == [2021, 2022, 2023, 2024]
    assert chunks[0].start == _dt(2021, 6, 15)
    assert chunks[0].end == _dt(2022, 1, 1)
    assert chunks[1].start == _dt(2022, 1, 1)
    assert chunks[1].end == _dt(2023, 1, 1)
    assert chunks[-1].start == _dt(2024, 1, 1)
    assert chunks[-1].end == _dt(2024, 3, 10)


def test_year_chunks_exact_year_boundary_no_empty_chunk():
    chunks = year_chunks(_dt(2022, 1, 1), _dt(2023, 1, 1))
    assert len(chunks) == 1
    assert chunks[0].year == 2022


def test_year_chunk_unit_key():
    chunks = year_chunks(_dt(2023, 1, 1), _dt(2024, 1, 1))
    assert chunks[0].unit_key == "2023"


def test_month_chunks_single_month():
    chunks = month_chunks(_dt(2023, 3, 5), _dt(2023, 3, 20))
    assert len(chunks) == 1
    assert chunks[0].unit_key == "2023-03"
    assert chunks[0].start == _dt(2023, 3, 5)
    assert chunks[0].end == _dt(2023, 3, 20)


def test_month_chunks_spans_year_boundary():
    chunks = month_chunks(_dt(2023, 11, 15), _dt(2024, 2, 10))
    assert [c.unit_key for c in chunks] == ["2023-11", "2023-12", "2024-01", "2024-02"]
    assert chunks[0].start == _dt(2023, 11, 15)
    assert chunks[0].end == _dt(2023, 12, 1)
    assert chunks[-1].start == _dt(2024, 2, 1)
    assert chunks[-1].end == _dt(2024, 2, 10)


def test_month_chunks_exact_month_boundary_no_empty_chunk():
    chunks = month_chunks(_dt(2023, 2, 1), _dt(2023, 3, 1))
    assert len(chunks) == 1
    assert chunks[0].unit_key == "2023-02"


def test_batches_none_size_returns_single_batch():
    assert _batches(["A", "B", "C"], None) == [["A", "B", "C"]]


def test_batches_splits_into_fixed_size_groups():
    assert _batches(["A", "B", "C", "D", "E"], 2) == [["A", "B"], ["C", "D"], ["E"]]
