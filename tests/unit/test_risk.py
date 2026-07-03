import pytest

from rs_spy.algo.risk import (
    RiskManager,
    bias_size_multiplier,
    cap_shares,
    neutral_tighten_stop_long,
    neutral_tighten_stop_short,
    position_size,
    score_size_multiplier,
    stop_price_long,
    stop_price_short,
)
from rs_spy.bias.buckets import BEAR, BULL, STRONG_BEAR, STRONG_BULL


def test_stop_price_long_and_short():
    assert stop_price_long(entry=100.0, atr_m5=2.0) == pytest.approx(98.0)
    assert stop_price_short(entry=100.0, atr_m5=2.0) == pytest.approx(102.0)


def test_bias_size_multiplier():
    assert bias_size_multiplier(STRONG_BULL) == pytest.approx(1.0)
    assert bias_size_multiplier(STRONG_BEAR) == pytest.approx(1.0)
    assert bias_size_multiplier(BULL) == pytest.approx(0.75)
    assert bias_size_multiplier(BEAR) == pytest.approx(0.75)


def test_score_size_multiplier_maps_50_to_100_onto_0p7_to_1p0():
    assert score_size_multiplier(50.0) == pytest.approx(0.7)
    assert score_size_multiplier(100.0) == pytest.approx(1.0)
    assert score_size_multiplier(75.0) == pytest.approx(0.85)
    # clips outside [50, 100]
    assert score_size_multiplier(20.0) == pytest.approx(0.7)
    assert score_size_multiplier(150.0) == pytest.approx(1.0)


def test_position_size_matches_hand_computed_example():
    # equity=100k, risk 0.5% => $500 risk. stop_distance=2.0 => base_shares=250.
    # BULL (not STRONG) => bias_mult=0.75. score=75 => score_mult=0.85. LONG => side_mult=1.0.
    # 250 * 0.75 * 0.85 * 1.0 = 159.375
    shares = position_size(
        equity=100_000.0,
        risk_per_trade_pct=0.005,
        stop_distance=2.0,
        bias=BULL,
        score=75.0,
        direction="LONG",
    )
    assert shares == pytest.approx(159.375)


def test_position_size_short_applies_side_multiplier():
    long_shares = position_size(100_000.0, 0.005, 2.0, STRONG_BULL, 100.0, "LONG")
    short_shares = position_size(100_000.0, 0.005, 2.0, STRONG_BEAR, 100.0, "SHORT")
    assert short_shares == pytest.approx(long_shares * 0.75)


def test_position_size_zero_stop_distance_returns_zero():
    assert position_size(100_000.0, 0.005, 0.0, BULL, 75.0, "LONG") == 0.0


def test_cap_shares_notional_cap_binds():
    # equity=100k, max_notional_pct=0.20 => $20k notional cap. entry=50 => 400 shares cap.
    capped = cap_shares(
        shares=1000.0,
        entry_price=50.0,
        equity=100_000.0,
        adv20=10_000_000.0,
        expected_hold_minutes=120.0,
    )
    assert capped == pytest.approx(400.0)


def test_cap_shares_participation_cap_binds():
    # adv20=1,000,000; 5% of that = 50,000; /390*120 = 15,384.6...
    capped = cap_shares(
        shares=1_000_000.0,
        entry_price=1.0,
        equity=100_000_000.0,
        adv20=1_000_000.0,
        expected_hold_minutes=120.0,
    )
    assert capped == pytest.approx(15384.0, abs=1.0)


def test_cap_shares_floors_to_whole_share():
    capped = cap_shares(159.9, entry_price=10.0, equity=1_000_000.0, adv20=10_000_000.0, expected_hold_minutes=120.0)
    assert capped == 159.0


def test_neutral_tighten_stop_long_moves_toward_price_only():
    # entry=100, atr=4 => candidate = 100 - 0.5*4 = 98. current_stop=95 (looser) => tightens to 98.
    tightened = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=95.0, current_price=101.0)
    assert tightened == pytest.approx(98.0)
    # current_stop already tighter than candidate => stays put (never loosens)
    tightened2 = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=99.0, current_price=101.0)
    assert tightened2 == pytest.approx(99.0)


def test_neutral_tighten_stop_long_uses_breakeven_when_favorable_enough():
    # price has moved up by >= 0.5*atr (2.0) beyond entry => breakeven (100) is used
    # instead of the plain candidate (98), since it's the more protective of the two.
    tightened = neutral_tighten_stop_long(entry=100.0, atr_m5=4.0, current_stop=90.0, current_price=103.0)
    assert tightened == pytest.approx(100.0)


def test_neutral_tighten_stop_short_mirrors_long():
    tightened = neutral_tighten_stop_short(entry=100.0, atr_m5=4.0, current_stop=105.0, current_price=99.0)
    assert tightened == pytest.approx(102.0)
    tightened2 = neutral_tighten_stop_short(entry=100.0, atr_m5=4.0, current_stop=101.0, current_price=99.0)
    assert tightened2 == pytest.approx(101.0)


def test_risk_manager_consecutive_stopouts_halt_new_entries_for_24_bars():
    rm = RiskManager(starting_equity=100_000.0)
    assert rm.can_enter(bar_index=0)
    rm.register_exit(pnl=-100.0, equity=99_900.0, exit_reason="hard_stop", bar_index=10)
    rm.register_exit(pnl=-100.0, equity=99_800.0, exit_reason="hard_stop", bar_index=20)
    assert rm.can_enter(bar_index=25)  # only 2 so far
    rm.register_exit(pnl=-100.0, equity=99_700.0, exit_reason="hard_stop", bar_index=30)
    assert not rm.can_enter(bar_index=31)
    assert not rm.can_enter(bar_index=53)  # 30 + 24 - 1
    assert rm.can_enter(bar_index=54)  # 30 + 24


def test_risk_manager_non_stopout_exit_resets_consecutive_count():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(-100.0, 99_900.0, "hard_stop", 1)
    rm.register_exit(-100.0, 99_800.0, "hard_stop", 2)
    rm.register_exit(50.0, 99_850.0, "profit_take", 3)
    rm.register_exit(-100.0, 99_750.0, "hard_stop", 4)
    assert rm.can_enter(bar_index=5)  # count reset to 1 after the profit_take


def test_risk_manager_daily_loss_limit_halts_until_new_session():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(pnl=-2_100.0, equity=97_900.0, exit_reason="rs_failure", bar_index=1)
    assert not rm.can_enter(bar_index=2)
    rm.new_session(equity=97_900.0)
    assert rm.can_enter(bar_index=3)


def test_risk_manager_weekly_loss_limit_halts_until_new_week():
    rm = RiskManager(starting_equity=100_000.0)
    rm.register_exit(pnl=-4_100.0, equity=95_900.0, exit_reason="rs_failure", bar_index=1)
    assert not rm.can_enter(bar_index=2)
    rm.new_session(equity=95_900.0)
    assert not rm.can_enter(bar_index=3)  # weekly halt survives a new session
    rm.new_week(equity=95_900.0)
    assert rm.can_enter(bar_index=4)
