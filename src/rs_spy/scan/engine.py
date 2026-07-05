"""Universe-scan engine: per-symbol metrics (SQL, Task 4) + gate application.

Gate evaluation is first-fail attributed in GATE_ORDER so the funnel
partitions exactly: every evaluated symbol lands in exactly one of
fail_<gate> or passed (tested by the funnel-partition test).
"""
from dataclasses import dataclass

import duckdb
import pandas as pd

from rs_spy.scan.config import ScanConfig

GATE_ORDER = ("listing", "coverage", "price", "adv_shares", "adv_dollars")


def apply_gates(
    assets: pd.DataFrame, metrics: pd.DataFrame, config: ScanConfig
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join asset metadata with as-of metrics and apply 01 §4's gates.

    Returns (evaluated, funnel): `evaluated` indexed by symbol with a bool
    `passed` and a `first_fail` gate name (None when passed); `funnel` counts
    every symbol exactly once.
    """
    ev = assets.set_index("symbol").join(metrics, how="left")
    sym = ev.index.to_series()

    name_pattern = "|".join(f"(?:{p})" for p in config.name_blocklist)
    listing_ok = (
        ev["tradable"].fillna(False)
        & ev["exchange"].isin(config.exchange_allowlist)
        & ~ev["name"].fillna("").str.contains(name_pattern, case=False, regex=True)
        & ~sym.str.endswith(tuple(config.symbol_suffix_blocklist))
        & ~sym.isin(config.symbol_denylist)
    )
    gate_ok = {
        "listing": listing_ok,
        "coverage": ev["n_bars"].fillna(0) >= config.adv_window,
        "price": (ev["last_close"] >= config.min_price).fillna(False),
        "adv_shares": (ev["adv_shares"] >= config.min_adv_shares).fillna(False),
        "adv_dollars": (ev["adv_dollars"] >= config.min_adv_dollars).fillna(False),
    }

    first_fail = pd.Series([None] * len(ev.index), index=ev.index, dtype=object)
    remaining = pd.Series(True, index=ev.index)
    funnel: dict[str, int] = {"assets": int(len(ev))}
    for gate in GATE_ORDER:
        failed_here = remaining & ~gate_ok[gate]
        first_fail[failed_here] = gate
        funnel[f"fail_{gate}"] = int(failed_here.sum())
        remaining &= gate_ok[gate]
    ev["passed"] = remaining
    ev["first_fail"] = first_fail
    funnel["passed"] = int(remaining.sum())
    return ev, funnel


class ScanCoverageError(RuntimeError):
    """Refusal to emit a snapshot: too few listing-eligible symbols have a bar
    for as_of (holiday, half-day quirk, or upstream data outage)."""


@dataclass(frozen=True)
class ScanResult:
    as_of: pd.Timestamp
    evaluated: pd.DataFrame
    funnel: dict

    @property
    def passing(self) -> list[str]:
        return sorted(self.evaluated.index[self.evaluated["passed"]])


def compute_scan_metrics(
    con: "duckdb.DuckDBPyConnection", as_of, adv_window: int = 20
) -> pd.DataFrame:
    """Per-symbol as-of metrics from cached daily bars.

    Causality by construction: the WHERE clause admits only bars dated <= as_of
    (daily bars are timestamped at midnight ET = 04:00/05:00 UTC, so CAST(ts AS
    DATE) is the ET session date). The ADV window is the symbol's last
    `adv_window` BARS, not calendar days (see task note). A tz-aware `as_of`
    is converted to its UTC calendar date (the module's date convention).
    """
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is not None:
        as_of = as_of.tz_convert("UTC").tz_localize(None)
    as_of_date = as_of.date()
    df = con.execute(
        """
        WITH ranked AS (
            SELECT symbol, ts, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY ts DESC) AS rn
            FROM bars
            WHERE timespan = 'day' AND CAST(ts AS DATE) <= ?
        )
        SELECT symbol,
               max(CASE WHEN rn = 1 THEN close END)            AS last_close,
               max(CASE WHEN rn = 1 THEN CAST(ts AS DATE) END) AS last_bar_date,
               avg(volume)         FILTER (WHERE rn <= ?)      AS adv_shares,
               avg(close * volume) FILTER (WHERE rn <= ?)      AS adv_dollars,
               count(*)            FILTER (WHERE rn <= ?)      AS n_bars
        FROM ranked
        GROUP BY symbol
        """,
        [as_of_date, adv_window, adv_window, adv_window],
    ).df()
    df["last_bar_date"] = pd.to_datetime(df["last_bar_date"])
    df["n_bars"] = df["n_bars"].astype(int)
    return df.set_index("symbol")


def run_universe_scan(
    con: "duckdb.DuckDBPyConnection",
    assets: pd.DataFrame,
    as_of,
    config: ScanConfig | None = None,
) -> ScanResult:
    """The nightly scan and the point-in-time reconstruction -- one code path.

    as_of=today against tonight's refreshed bars is the live scan; as_of=any
    past trading date reconstructs the universe as it would have been (with
    the disclosed survivorship limit: `assets` is always the CURRENT listing).

    `as_of` may be tz-aware (the codebase's "now" convention is
    datetime.now(timezone.utc), see data/manifest.py); it is normalized to a
    naive UTC calendar timestamp up front so the last_bar_date comparison
    below (tz-naive, from CAST(ts AS DATE)) never silently evaluates
    naive-vs-aware equality as all-False.
    """
    config = config or ScanConfig()
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is not None:
        as_of = as_of.tz_convert("UTC").tz_localize(None)
    metrics = compute_scan_metrics(con, as_of, adv_window=config.adv_window)
    evaluated, funnel = apply_gates(assets, metrics, config)

    listing_eligible = evaluated["first_fail"].ne("listing")
    if listing_eligible.any():
        have_asof = float(
            (evaluated.loc[listing_eligible, "last_bar_date"] == as_of.normalize()).mean()
        )
    else:
        have_asof = 0.0
    if have_asof < config.min_coverage_fraction:
        raise ScanCoverageError(
            f"only {have_asof:.0%} of listing-eligible symbols have a bar for "
            f"{as_of.date()} (< {config.min_coverage_fraction:.0%}) -- "
            "holiday, weekend, or data outage?"
        )
    return ScanResult(as_of=as_of, evaluated=evaluated, funnel=funnel)
