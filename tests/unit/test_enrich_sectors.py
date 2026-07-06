"""Sector enrichment: pure collection logic; yfinance is injected, never imported here."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "enrich_sectors.py"
spec = importlib.util.spec_from_file_location("enrich_sectors", SCRIPT)
enrich = importlib.util.module_from_spec(spec)
sys.modules["enrich_sectors"] = enrich
spec.loader.exec_module(enrich)


def test_collect_sectors_partitions_resolved_and_unresolved():
    def fake_fetch(sym):
        return {"HOOD": "Financial Services", "SOFI": "Financial Services"}.get(sym)

    sectors, unresolved = enrich.collect_sectors(["HOOD", "MYST", "SOFI"], fake_fetch)
    assert sectors == {"HOOD": "Financial Services", "SOFI": "Financial Services"}
    assert unresolved == ["MYST"]


def test_collect_sectors_treats_fetch_exceptions_as_unresolved():
    def flaky(sym):
        raise RuntimeError("rate limited")

    sectors, unresolved = enrich.collect_sectors(["AAA"], flaky)
    assert sectors == {}
    assert unresolved == ["AAA"]


def test_sectors_yaml_doc_is_sorted_and_carries_the_source_note():
    doc = enrich.sectors_yaml_doc({"B": "X", "A": "Y"}, source_note="yfinance 2026-07-05")
    assert doc["_source"] == "yfinance 2026-07-05"
    assert list(doc["sectors"].keys()) == ["A", "B"]


def test_make_lookup_direct_hit():
    fetch_sector = enrich.make_lookup({"HOOD": "Financial Services"})
    assert fetch_sector("HOOD") == "Financial Services"


def test_make_lookup_dot_to_slash_fallback():
    fetch_sector = enrich.make_lookup({"BRK/B": "Financial Services"})
    assert fetch_sector("BRK.B") == "Financial Services"


def test_make_lookup_miss_returns_none():
    fetch_sector = enrich.make_lookup({"HOOD": "Financial Services"})
    assert fetch_sector("NOPE") is None
