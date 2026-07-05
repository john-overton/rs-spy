"""Gate logic golden tests + funnel partition. Pure pandas -- no DuckDB here."""
import pandas as pd

from rs_spy.data.alpaca_client import ASSET_COLUMNS
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import GATE_ORDER, apply_gates

CFG = ScanConfig()  # iex defaults: min_price=10, adv_window=20


def _assets(rows):
    return pd.DataFrame(rows, columns=ASSET_COLUMNS)


def _asset_row(symbol, name="Good Corp Common Stock", exchange="NYSE", tradable=True):
    return {"symbol": symbol, "name": name, "exchange": exchange, "tradable": tradable,
            "shortable": True, "fractionable": True, "optionable": True}


def _metrics(entries):
    """entries: {symbol: (last_close, adv_shares, adv_dollars, n_bars)}"""
    if not entries:
        # Return empty DataFrame with correct schema and symbol as index
        return pd.DataFrame(
            columns=["last_close", "last_bar_date", "adv_shares", "adv_dollars", "n_bars"]
        ).set_index(pd.Index([], name="symbol"))
    df = pd.DataFrame(
        [
            {"symbol": s, "last_close": c, "last_bar_date": pd.Timestamp("2026-07-02"),
             "adv_shares": sh, "adv_dollars": d, "n_bars": n}
            for s, (c, sh, d, n) in entries.items()
        ]
    )
    return df.set_index("symbol")


GOOD = (50.0, CFG.min_adv_shares * 2, CFG.min_adv_dollars * 2, 20)


def test_each_gate_fails_exactly_the_symbol_built_to_fail_it():
    assets = _assets([
        _asset_row("PASS"),
        _asset_row("NOTRADE", tradable=False),
        _asset_row("ARCAETF", exchange="ARCA"),
        _asset_row("SPYLIKE", name="SPDR S&P 500 ETF Trust"),
        _asset_row("CHEAP"),
        _asset_row("THINVOL"),
        _asset_row("LOWDOLL"),
        _asset_row("YOUNG"),
    ])
    metrics = _metrics({
        "PASS": GOOD,
        "NOTRADE": GOOD,
        "ARCAETF": GOOD,
        "SPYLIKE": GOOD,
        "CHEAP": (9.99, GOOD[1], GOOD[2], 20),
        "THINVOL": (50.0, CFG.min_adv_shares / 2, GOOD[2], 20),
        "LOWDOLL": (50.0, GOOD[1], CFG.min_adv_dollars / 2, 20),
        "YOUNG": (50.0, GOOD[1], GOOD[2], 19),
    })
    ev, funnel = apply_gates(assets, metrics, CFG)
    assert ev.loc["PASS", "passed"] and ev.loc["PASS", "first_fail"] is None
    assert ev.loc["NOTRADE", "first_fail"] == "listing"
    assert ev.loc["ARCAETF", "first_fail"] == "listing"
    assert ev.loc["SPYLIKE", "first_fail"] == "listing"
    assert ev.loc["CHEAP", "first_fail"] == "price"
    assert ev.loc["THINVOL", "first_fail"] == "adv_shares"
    assert ev.loc["LOWDOLL", "first_fail"] == "adv_dollars"
    assert ev.loc["YOUNG", "first_fail"] == "coverage"


def test_ten_dollar_boundary_is_inclusive():
    assets = _assets([_asset_row("ATTEN")])
    ev, _ = apply_gates(assets, _metrics({"ATTEN": (10.0, GOOD[1], GOOD[2], 20)}), CFG)
    assert ev.loc["ATTEN", "passed"]


def test_reit_trust_names_are_not_blocked_but_etf_issuers_are():
    # "Trust" alone must NOT be in the blocklist: Camden Property Trust is a
    # legitimate S&P 500 common stock. ETF issuer brands + the word ETF are.
    assets = _assets([
        _asset_row("CPT", name="Camden Property Trust"),
        _asset_row("FAKE1", name="iShares Core Whatever"),
        _asset_row("FAKE2", name="ProShares UltraPro Something", exchange="NASDAQ"),
        _asset_row("QQQ", name="Invesco QQQ Trust, Series 1", exchange="NASDAQ"),
        _asset_row("BRK.B", name="Berkshire Hathaway Inc. Class B"),
        _asset_row("WTS.WS", name="Some Warrant"),
    ])
    metrics = _metrics({s: GOOD for s in ["CPT", "FAKE1", "FAKE2", "QQQ", "BRK.B", "WTS.WS"]})
    ev, _ = apply_gates(assets, metrics, CFG)
    assert ev.loc["CPT", "passed"]
    assert ev.loc["BRK.B", "passed"]  # class shares survive the suffix check
    assert ev.loc["FAKE1", "first_fail"] == "listing"
    assert ev.loc["FAKE2", "first_fail"] == "listing"
    assert ev.loc["QQQ", "first_fail"] == "listing"  # explicit symbol denylist
    assert ev.loc["WTS.WS", "first_fail"] == "listing"  # warrant suffix


def test_symbol_missing_from_metrics_fails_coverage_not_a_crash():
    assets = _assets([_asset_row("NEWIPO")])
    ev, funnel = apply_gates(assets, _metrics({}), CFG)
    assert ev.loc["NEWIPO", "first_fail"] == "coverage"


def test_funnel_partitions_exactly():
    assets = _assets([
        _asset_row("PASS"), _asset_row("NOTRADE", tradable=False), _asset_row("CHEAP"),
    ])
    metrics = _metrics({"PASS": GOOD, "NOTRADE": GOOD, "CHEAP": (5.0, GOOD[1], GOOD[2], 20)})
    ev, funnel = apply_gates(assets, metrics, CFG)
    assert funnel["assets"] == 3
    assert funnel["assets"] == funnel["passed"] + sum(funnel[f"fail_{g}"] for g in GATE_ORDER)
