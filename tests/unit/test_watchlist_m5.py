import pytest

from rs_spy.selection.watchlist import (
    DIP_ARMED,
    ENTRY_EVAL,
    IDLE,
    QUALIFIED,
    apply_trigger_bypass,
    next_state_long,
    next_state_short,
)


def test_next_state_long_arms_on_lrsi_cross_even_without_rrs_cross():
    # RRS never crosses zero (stays positive throughout), but LRSI does the
    # 04 §6 "or LRSI < 20 then > 20" dip-reset -- should still arm.
    state = next_state_long(
        QUALIFIED, gate_pass=True, score=60.0, rrs_prev=1.0, rrs_now=1.2, lrsi_prev=15.0, lrsi_now=25.0
    )
    assert state == DIP_ARMED


def test_next_state_long_lrsi_none_falls_back_to_rrs_only_behavior():
    # both lrsi args omitted -- must reproduce the exact D1 behavior with no
    # regression (rrs doesn't cross, stays QUALIFIED).
    state = next_state_long(QUALIFIED, gate_pass=True, score=60.0, rrs_prev=1.0, rrs_now=1.2)
    assert state == QUALIFIED


def test_next_state_short_arms_on_lrsi_cross_down_through_80():
    state = next_state_short(
        QUALIFIED, gate_pass=True, score=60.0, rrs_prev=-1.0, rrs_now=-1.2, lrsi_prev=85.0, lrsi_now=75.0
    )
    assert state == DIP_ARMED


def test_next_state_long_rejects_old_positional_calling_convention():
    # Regression for the Task 8 review finding: lrsi_prev/lrsi_now were
    # inserted as positional params 6-7, ahead of the pre-existing
    # min_list_score/min_hold_score (now shifted to 8-9). A caller still
    # passing min_list_score/min_hold_score positionally as args 6-7 (the
    # old convention, e.g. `next_state_long(state, gp, score, rrs_prev,
    # rrs_now, min_list_score, min_hold_score)`) would have those values
    # silently misbind to lrsi_prev/lrsi_now instead, discarding the real
    # thresholds. lrsi_prev/lrsi_now/min_list_score/min_hold_score are now
    # keyword-only, so this must raise TypeError instead of silently
    # misbinding.
    with pytest.raises(TypeError):
        next_state_long(QUALIFIED, True, 60.0, 1.0, 1.2, 50.0, 40.0)


def test_apply_trigger_bypass_sends_qualified_direct_to_entry_eval():
    assert apply_trigger_bypass(QUALIFIED, gate_pass=True, trigger_matches_direction=True) == ENTRY_EVAL


def test_apply_trigger_bypass_leaves_other_states_alone():
    assert apply_trigger_bypass(IDLE, gate_pass=True, trigger_matches_direction=True) == IDLE
    assert apply_trigger_bypass(DIP_ARMED, gate_pass=True, trigger_matches_direction=True) == DIP_ARMED
    assert apply_trigger_bypass(QUALIFIED, gate_pass=True, trigger_matches_direction=False) == QUALIFIED
    assert apply_trigger_bypass(QUALIFIED, gate_pass=False, trigger_matches_direction=True) == QUALIFIED


def test_next_state_long_hold_gate_pass_keeps_qualified_through_intraday_gate_failure():
    from rs_spy.selection.watchlist import DIP_ARMED, QUALIFIED, next_state_long

    # full gate fails (rrs_m5 dipped) but the hold gate (D1-only) still passes:
    # symbol must stay QUALIFIED instead of demoting to IDLE...
    state = next_state_long(QUALIFIED, False, 60.0, 1.0, 1.0, hold_gate_pass=True)
    assert state == QUALIFIED
    # ...and the RRS zero-cross must now be able to arm on a later bar
    state = next_state_long(QUALIFIED, False, 60.0, -0.5, 0.2, hold_gate_pass=True)
    assert state == DIP_ARMED


def test_next_state_long_hold_gate_pass_false_still_demotes():
    from rs_spy.selection.watchlist import IDLE, QUALIFIED, next_state_long

    assert next_state_long(QUALIFIED, False, 60.0, 1.0, 1.0, hold_gate_pass=False) == IDLE


def test_next_state_long_idle_admission_ignores_hold_gate():
    from rs_spy.selection.watchlist import IDLE, next_state_long

    # hold_gate_pass=True must NOT admit a symbol whose full gates fail
    assert next_state_long(IDLE, False, 60.0, 1.0, 1.0, hold_gate_pass=True) == IDLE
