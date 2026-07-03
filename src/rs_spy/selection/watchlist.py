"""Per-symbol watchlist state machine + daily tradeable-list construction.
algo-spec/04-stock-selection-engine.md §5-6.

D1 simplifications vs. the full spec: dip-arming uses the raw per-bar D1 RRS
crossing zero (the D1 analog of "RRS(t) crosses <0 then >0"; LRSI is M5-only
and unavailable at D1 cadence). At M5 cadence, `next_state_long`/
`next_state_short` accept optional `lrsi_prev`/`lrsi_now` -- when both are
given, 04 §6's "or LRSI < 20 then > 20" (long) / "LRSI > 80 then < 80"
(short) OR-condition arms the dip in addition to the RRS-crossing check;
omitting them (as the D1 caller does) reproduces the RRS-only D1 behavior
exactly. `apply_trigger_bypass` implements 04 §6's trigger-day exception:
on a matching LONG_TRIGGER/SHORT_TRIGGER from the bias engine, a QUALIFIED
symbol goes straight to ENTRY_EVAL without waiting for its own dip ("the
market pullback itself was the dip"). List "stickiness" (04 §5.5 -- an
already-listed symbol keeps its slot unless it drops below the hold
threshold) is not implemented; each day's tradeable list is rebuilt fresh
from that day's scores.
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
    lrsi_prev: float | None = None,
    lrsi_now: float | None = None,
    min_list_score: float = 50.0,
    min_hold_score: float = 40.0,
) -> str:
    holds = gate_pass and _score_ok(score, min_hold_score)
    if state == IDLE:
        return QUALIFIED if gate_pass and _score_ok(score, min_list_score) else IDLE
    if not holds:
        return IDLE
    if state == QUALIFIED:
        rrs_crossed_up = rrs_prev is not None and rrs_now is not None and rrs_prev < 0 <= rrs_now
        lrsi_crossed_up = lrsi_prev is not None and lrsi_now is not None and lrsi_prev < 20 <= lrsi_now
        return DIP_ARMED if (rrs_crossed_up or lrsi_crossed_up) else QUALIFIED
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
    lrsi_prev: float | None = None,
    lrsi_now: float | None = None,
    min_list_score: float = 50.0,
    min_hold_score: float = 40.0,
) -> str:
    holds = gate_pass and _score_ok(score, min_hold_score)
    if state == IDLE:
        return QUALIFIED if gate_pass and _score_ok(score, min_list_score) else IDLE
    if not holds:
        return IDLE
    if state == QUALIFIED:
        rrs_crossed_down = rrs_prev is not None and rrs_now is not None and rrs_prev > 0 >= rrs_now
        lrsi_crossed_down = lrsi_prev is not None and lrsi_now is not None and lrsi_prev > 80 >= lrsi_now
        return DIP_ARMED if (rrs_crossed_down or lrsi_crossed_down) else QUALIFIED
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


def apply_trigger_bypass(state: str, gate_pass: bool, trigger_matches_direction: bool) -> str:
    """04 §6 trigger-day exception: a QUALIFIED symbol on the tradeable list
    goes straight to ENTRY_EVAL on a matching bias-engine trigger, bypassing
    its own individual dip-arm cycle. Only applies from QUALIFIED with gates
    still green; any other state is untouched (a symbol already DIP_ARMED or
    ENTRY_EVAL is already on its way in; IDLE symbols aren't list members)."""
    if state == QUALIFIED and gate_pass and trigger_matches_direction:
        return ENTRY_EVAL
    return state
