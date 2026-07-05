"""Universe-scan engine: per-symbol metrics (SQL, Task 4) + gate application.

Gate evaluation is first-fail attributed in GATE_ORDER so the funnel
partitions exactly: every evaluated symbol lands in exactly one of
fail_<gate> or passed (tested by the funnel-partition test).
"""
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
