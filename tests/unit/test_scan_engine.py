"""Gate logic golden tests + funnel partition. Pure pandas -- no DuckDB here."""
from pathlib import Path

import pandas as pd
import pytest

from rs_spy.data.alpaca_client import ASSET_COLUMNS
from rs_spy.data.ingest import _write_bars
from rs_spy.scan.bars import connect_scan
from rs_spy.scan.config import ScanConfig
from rs_spy.scan.engine import GATE_ORDER, ScanCoverageError, apply_gates, compute_scan_metrics, run_universe_scan

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


def test_issuer_named_common_stocks_pass_the_listing_gate():
    # QQQ's issuer is Invesco, so a future edit could plausibly add "Invesco"
    # (or "WisdomTree") to DEFAULT_NAME_BLOCKLIST -- which would silently kill
    # the issuers' own common stocks (IVZ, WT). ETF exclusion must come from
    # the ETF-word patterns, pure-ETF issuer brands, and the symbol denylist,
    # NOT from asset-manager company names.
    assets = _assets([
        _asset_row("IVZ", name="Invesco Ltd."),
        _asset_row("WT", name="WisdomTree Investments, Inc."),
    ])
    metrics = _metrics({"IVZ": GOOD, "WT": GOOD})
    ev, _ = apply_gates(assets, metrics, CFG)
    assert ev.loc["IVZ", "passed"] and ev.loc["IVZ", "first_fail"] is None
    assert ev.loc["WT", "passed"] and ev.loc["WT", "first_fail"] is None


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


# ---------------------------------------------------------------- as-of / SQL half


def _bar_frame(symbol, dates, close=50.0, volume=100_000):
    return pd.DataFrame(
        {
            "symbol": symbol, "timespan": "day",
            "ts": pd.DatetimeIndex(dates, tz="UTC"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "vwap": close, "trade_count": 100,
        }
    )


def _seeded_con(frames):
    con = connect_scan(Path(":memory:"))
    for f in frames:
        _write_bars(con, f)
    return con


DAYS = pd.bdate_range("2026-05-01", periods=30)


def test_compute_scan_metrics_uses_only_bars_at_or_before_as_of():
    con = _seeded_con([_bar_frame("AAA", DAYS, close=50.0, volume=100_000)])
    as_of = DAYS[19]  # bar #20 of 30
    m = compute_scan_metrics(con, as_of, adv_window=20)
    assert m.loc["AAA", "n_bars"] == 20
    assert m.loc["AAA", "last_bar_date"] == as_of


def test_no_lookahead_future_bars_do_not_change_the_scan():
    """The spec's 'no future bias' guarantee, tested the same way the
    indicator causality tests work: truncate vs full history, same answer."""
    full = _seeded_con([_bar_frame("AAA", DAYS)])
    truncated = _seeded_con([_bar_frame("AAA", DAYS[:20])])
    as_of = DAYS[19]
    m_full = compute_scan_metrics(full, as_of, adv_window=20)
    m_trunc = compute_scan_metrics(truncated, as_of, adv_window=20)
    pd.testing.assert_frame_equal(m_full, m_trunc)


def test_adv_uses_the_trailing_window_only():
    # 40 bars: first 20 at volume 1M, last 20 at volume 10k. As of the end,
    # ADV must reflect only the trailing 20 bars.
    days = pd.bdate_range("2026-04-01", periods=40)
    f = pd.concat([
        _bar_frame("AAA", days[:20], volume=1_000_000),
        _bar_frame("AAA", days[20:], volume=10_000),
    ])
    con = _seeded_con([f])
    m = compute_scan_metrics(con, days[-1], adv_window=20)
    assert m.loc["AAA", "adv_shares"] == 10_000


def test_run_universe_scan_end_to_end_pass_and_coverage_refusal():
    assets = _assets([_asset_row("AAA"), _asset_row("BBB")])
    con = _seeded_con([
        _bar_frame("AAA", DAYS, close=50.0, volume=int(CFG.min_adv_shares * 2)),
        _bar_frame("BBB", DAYS, close=50.0, volume=int(CFG.min_adv_shares * 2)),
    ])
    result = run_universe_scan(con, assets, DAYS[-1], CFG)
    assert result.passing == ["AAA", "BBB"]
    assert result.funnel["passed"] == 2

    # a non-trading date (weekend after DAYS[-1]) -> no symbol has an as-of
    # bar -> the scan must refuse rather than emit a stale/empty snapshot
    weekend = DAYS[-1] + pd.Timedelta(days=2)
    assert weekend.dayofweek in (5, 6)
    with pytest.raises(ScanCoverageError):
        run_universe_scan(con, assets, weekend, CFG)
