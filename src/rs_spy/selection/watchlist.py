"""Per-symbol watchlist state machine + daily tradeable-list construction.
algo-spec/04-stock-selection-engine.md §5-6.

D1 simplifications vs. the full spec: dip-arming uses the raw per-bar D1 RRS
crossing zero (the D1 analog of "RRS(t) crosses <0 then >0"; LRSI is M5-only
and unavailable here). List "stickiness" (04 §5.5 -- an already-listed symbol
keeps its slot unless it drops below the hold threshold) is not implemented;
each day's tradeable list is rebuilt fresh from that day's scores.
"""
import pandas as pd

IDLE = "IDLE"
QUALIFIED = "QUALIFIED"
DIP_ARMED = "DIP_ARMED"
ENTRY_EVAL = "ENTRY_EVAL"


def _score_ok(score: float | None, threshold: float) -> bool:
    return score is not None and not pd.isna(score) and score >= threshold


def next_state_long(
    state: str,
    gate_pass: bool,
    score: float | None,
    rrs_prev: float | None,
    rrs_now: float | None,
    min_list_score: float = 50.0,
    min_hold_score: float = 40.0,
) -> str:
    holds = gate_pass and _score_ok(score, min_hold_score)
    if state == IDLE:
        return QUALIFIED if gate_pass and _score_ok(score, min_list_score) else IDLE
    if not holds:
        return IDLE
    if state == QUALIFIED:
        crossed_up = rrs_prev is not None and rrs_now is not None and rrs_prev < 0 <= rrs_now
        return DIP_ARMED if crossed_up else QUALIFIED
    if state == DIP_ARMED:
        return ENTRY_EVAL
    if state == ENTRY_EVAL:
        return QUALIFIED  # evaluated (entered or not); re-arm for the next dip
    return IDLE


def next_state_short(
    state: str,
    gate_pass: bool,
    score: float | None,
    rrs_prev: float | None,
    rrs_now: float | None,
    min_list_score: float = 50.0,
    min_hold_score: float = 40.0,
) -> str:
    holds = gate_pass and _score_ok(score, min_hold_score)
    if state == IDLE:
        return QUALIFIED if gate_pass and _score_ok(score, min_list_score) else IDLE
    if not holds:
        return IDLE
    if state == QUALIFIED:
        crossed_down = rrs_prev is not None and rrs_now is not None and rrs_prev > 0 >= rrs_now
        return DIP_ARMED if crossed_down else QUALIFIED
    if state == DIP_ARMED:
        return ENTRY_EVAL
    if state == ENTRY_EVAL:
        return QUALIFIED
    return IDLE


def build_tradeable_list(
    scores: dict[str, float],
    sectors: dict[str, str],
    min_list_score: float = 50.0,
    top_n_list: int = 20,
    top_n_tradeable: int = 5,
    max_per_sector: int = 2,
) -> list[str]:
    candidates = sorted(
        (
            (sym, score)
            for sym, score in scores.items()
            if score is not None and not pd.isna(score) and score >= min_list_score
        ),
        key=lambda pair: -pair[1],
    )[:top_n_list]

    tradeable: list[str] = []
    sector_count: dict[str, int] = {}
    for sym, _score in candidates:
        sector = sectors.get(sym, "Unknown")
        if sector_count.get(sector, 0) >= max_per_sector:
            continue
        tradeable.append(sym)
        sector_count[sector] = sector_count.get(sector, 0) + 1
        if len(tradeable) >= top_n_tradeable:
            break
    return tradeable
