"""M10 universe-500 selection (pure logic; scripts/build_universe_500.py is the shell).

Selection rule (spec 2026-07-05-universe-500-design.md): curated 130 verbatim +
top-up from the latest scan's passing set ranked by adv_dollars desc, requiring
a first daily bar <= HISTORY_CUTOFF (continuous 5-year history -- the same rule
the curated 130 were hand-picked under; new listings create data gaps that
starve the backtest), until TARGET_SIZE total trade symbols. Deterministic:
ties break alphabetically; no RNG anywhere.
"""
import pandas as pd

from rs_spy.universe import Universe

HISTORY_CUTOFF = pd.Timestamp("2021-07-05")
TARGET_SIZE = 500


def select_topup(
    snapshot: pd.DataFrame,
    first_bar: dict,
    curated: list[str],
    *,
    history_cutoff: pd.Timestamp = HISTORY_CUTOFF,
    target: int = TARGET_SIZE,
) -> list[str]:
    """Ranked top-up symbols: passing, not curated, first bar <= cutoff."""
    n_needed = max(0, target - len(curated))
    curated_set = set(curated)
    eligible = snapshot[snapshot["passed"] == True]  # noqa: E712 -- pandas mask
    eligible = eligible[~eligible["symbol"].isin(curated_set)]
    eligible = eligible[eligible["symbol"].map(
        lambda s: s in first_bar and pd.Timestamp(first_bar[s]) <= history_cutoff
    )]
    ranked = eligible.sort_values(
        ["adv_dollars", "symbol"], ascending=[False, True]
    )
    return ranked["symbol"].head(n_needed).tolist()


def build_universe_yaml(
    curated_universe: Universe, topup: list[str], sectors: dict[str, str]
) -> dict:
    """universe.yaml-schema dict: same benchmarks, curated entries verbatim,
    then top-up entries labeled from `sectors` (UNKNOWN fallback)."""
    dupes = sorted(set(topup) & set(curated_universe.trade_symbols))
    if dupes:
        raise ValueError(f"top-up duplicates curated symbols: {dupes}")
    return {
        "benchmarks": [b.model_dump() for b in curated_universe.benchmarks],
        "universe": [
            *[s.model_dump() for s in curated_universe.universe],
            *[{"symbol": sym, "sector": sectors.get(sym, "UNKNOWN")} for sym in topup],
        ],
    }
