"""Universe-500 selection: scan-ranked top-up with a hard history cutoff."""
import pandas as pd
import pytest

from rs_spy.scan.universe500 import (
    HISTORY_CUTOFF,
    TARGET_SIZE,
    build_universe_yaml,
    select_topup,
)
from rs_spy.universe import Universe

CUTOFF = pd.Timestamp("2021-07-05")
OLD = pd.Timestamp("2020-01-02")   # long-listed
NEW = pd.Timestamp("2024-03-01")   # recent IPO -> excluded


def _snapshot(rows):
    return pd.DataFrame(rows, columns=["symbol", "passed", "adv_dollars"])


def test_topup_ranks_by_adv_dollars_and_applies_all_filters():
    snap = _snapshot([
        ("BIG", True, 9e9), ("MID", True, 5e9), ("SML", True, 1e9),
        ("IPO", True, 8e9),          # fails history cutoff
        ("FLK", False, 7e9),         # failed the scan
        ("AAPL", True, 6e9),         # already curated
    ])
    first_bar = {"BIG": OLD, "MID": OLD, "SML": OLD, "IPO": NEW, "FLK": OLD, "AAPL": OLD}
    out = select_topup(snap, first_bar, curated=["AAPL"], history_cutoff=CUTOFF, target=4)
    assert out == ["BIG", "MID", "SML"]  # target 4 - 1 curated = 3, ranked desc


def test_topup_is_deterministic_on_ties():
    snap = _snapshot([("BBB", True, 1e9), ("AAA", True, 1e9)])
    first_bar = {"AAA": OLD, "BBB": OLD}
    out = select_topup(snap, first_bar, curated=[], history_cutoff=CUTOFF, target=2)
    assert out == ["AAA", "BBB"]  # equal adv -> alphabetical


def test_topup_missing_first_bar_is_excluded_not_a_crash():
    snap = _snapshot([("GHOST", True, 9e9), ("REAL", True, 1e9)])
    out = select_topup(snap, {"REAL": OLD}, curated=[], history_cutoff=CUTOFF, target=5)
    assert out == ["REAL"]


def test_defaults_are_the_spec_values():
    assert HISTORY_CUTOFF == pd.Timestamp("2021-07-05")
    assert TARGET_SIZE == 500


def test_build_universe_yaml_keeps_curated_verbatim_then_topup_with_sectors():
    curated = Universe.model_validate({
        "benchmarks": [{"symbol": "SPY", "role": "primary"},
                       {"symbol": "QQQ", "role": "secondary"}],
        "universe": [{"symbol": "AAPL", "sector": "Technology"}],
    })
    doc = build_universe_yaml(curated, ["HOOD", "MYST"], {"HOOD": "Financials"})
    assert doc["benchmarks"] == [{"symbol": "SPY", "role": "primary"},
                                 {"symbol": "QQQ", "role": "secondary"}]
    assert doc["universe"][0] == {"symbol": "AAPL", "sector": "Technology"}
    assert doc["universe"][1] == {"symbol": "HOOD", "sector": "Financials"}
    assert doc["universe"][2] == {"symbol": "MYST", "sector": "UNKNOWN"}
    Universe.model_validate(doc)  # round-trips through the pydantic schema


def test_build_universe_yaml_rejects_duplicate_topup_of_curated():
    curated = Universe.model_validate({
        "benchmarks": [{"symbol": "SPY", "role": "primary"}],
        "universe": [{"symbol": "AAPL", "sector": "Technology"}],
    })
    with pytest.raises(ValueError, match="AAPL"):
        build_universe_yaml(curated, ["AAPL"], {})
