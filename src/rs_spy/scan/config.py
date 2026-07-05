"""Scan thresholds + listing heuristics (algo-spec 01 §4, with disclosed substitutions).

Feed presets: `sip` uses the spec's real thresholds (1M shares / $25M); `iex`
uses recalibrated proxies for the free tier's IEX-only volume (~2-3% of
consolidated -- same evidence base as BacktestConfigM5.min_adv_shares=50k).
The IEX defaults below are pre-calibration estimates; Task 9 calibrates them
against real cached data and updates them (with the measured numbers in a
comment) if the resulting universe size is far outside the spec's 800-1,500.

Heuristic listing filters (Alpaca has no security-type field):
  * exchange allowlist NYSE/NASDAQ/AMEX -- ARCA/BATS listings are
    overwhelmingly ETFs/ETNs;
  * name patterns for ETF words and pure-ETF issuer brands. "Trust" alone is
    deliberately NOT blocked (Camden Property Trust and other REITs are real
    common stocks); NASDAQ-listed ETFs that dodge the issuer patterns are
    caught case-by-case via symbol_denylist (QQQ today; extend as found --
    the universe_snapshots table makes any slip visible);
  * symbol suffixes for warrants/units/rights.
The float>=50M gate (01 §4.4) is SUBSTITUTED by the dollar-volume floor (no
float data on Alpaca); the halt-history gate (01 §4.5) is DROPPED (no
historical halt feed). Both disclosed in the spec and scan/__init__.py.
"""
from dataclasses import dataclass

IEX_MIN_ADV_SHARES = 30_000.0
IEX_MIN_ADV_DOLLARS = 750_000.0
SIP_MIN_ADV_SHARES = 1_000_000.0
SIP_MIN_ADV_DOLLARS = 25_000_000.0

DEFAULT_EXCHANGE_ALLOWLIST = frozenset({"NYSE", "NASDAQ", "AMEX"})
DEFAULT_NAME_BLOCKLIST = (
    r"\bETF\b",
    r"\bETN\b",
    r"\bFund\b",
    r"\bIndex\b",
    "iShares",
    "ProShares",
    "SPDR",
    "Direxion",
    "Vanguard",
)
DEFAULT_SYMBOL_DENYLIST = frozenset({"QQQ"})
DEFAULT_SYMBOL_SUFFIX_BLOCKLIST = (".WS", ".U", ".RT")


@dataclass(frozen=True)
class ScanConfig:
    feed: str = "iex"
    min_price: float = 10.0
    adv_window: int = 20
    min_adv_shares: float = IEX_MIN_ADV_SHARES
    min_adv_dollars: float = IEX_MIN_ADV_DOLLARS
    exchange_allowlist: frozenset = DEFAULT_EXCHANGE_ALLOWLIST
    name_blocklist: tuple = DEFAULT_NAME_BLOCKLIST
    symbol_denylist: frozenset = DEFAULT_SYMBOL_DENYLIST
    symbol_suffix_blocklist: tuple = DEFAULT_SYMBOL_SUFFIX_BLOCKLIST
    min_coverage_fraction: float = 0.80

    @classmethod
    def for_feed(cls, feed: str) -> "ScanConfig":
        if feed == "iex":
            return cls()
        if feed == "sip":
            return cls(
                feed="sip",
                min_adv_shares=SIP_MIN_ADV_SHARES,
                min_adv_dollars=SIP_MIN_ADV_DOLLARS,
            )
        raise ValueError(f"unknown feed {feed!r}: expected 'iex' or 'sip'")
