"""Position sizing + account-level risk guards. algo-spec/07-risk-management.md.

Implements what a historical-bar backtest can meaningfully simulate: §2 position
sizing, §3 stops (ATR-only distance -- see below), the §1 daily/weekly loss limits
and consecutive-stop-out halt, and §4's two dynamic-tightening rules (this module
has the NEUTRAL-bias stop-tightening formula; the final-30-minute profit-take
reduction lives in algo/long.py|short.py since it's evaluated alongside the other
exit rules, not here).

Deliberately NOT implemented: §6's kill switches (data-feed-gap detection,
broker-reject retry, clock-skew halt, halted-symbol handling) are live-trading
operational concerns with no analog against clean historical bars already sitting
in the warehouse -- there is no "feed" to gap in a backtest.

Stop placement (§3) is simplified: the spec's "lowest of the qualifying dip's swing
low and (entry - 1.0*ATR_M5)" alternative is reduced to the ATR-only distance --
this project doesn't track "the qualifying dip's swing low" as distinct structure
(matching backtest/engine.py's own D1 stop precedent, which is also ATR-only
distance rather than structure-based). Consequence: the resulting stop distance is
`stop_atr_mult`xATR (default 1.0), deliberately never clamped to the spec's
1.5xATR cap so swept `stop_atr_mult` values are honored; the cap and the "skip the
entry rather than widen the stop" branch belong to the not-implemented swing-low
variant -- disclosed, not silently dropped.

"or breakeven if better" (§4's NEUTRAL-bias tightening rule) is a discretionary
four words with no formula given. Read here as: tighten toward entry - 0.5*ATR,
but if the position has already moved favorably by at least that same 0.5*ATR
beyond entry, tighten to breakeven instead (the more protective of the two once
that much cushion exists) -- and never move the stop away from price, matching
§2's "stops are never moved away from price."
"""
from dataclasses import dataclass, field

from rs_spy.bias.buckets import STRONG_BEAR, STRONG_BULL

STOP_ATR_MULT = 1.0
STOP_ATR_CAP_MULT = 1.5  # reserved for the spec's swing-low stop variant (07 §3 cap); not applied to the ATR-only stop -- a swept stop_atr_mult must never be silently clamped
NEUTRAL_TIGHTEN_ATR_MULT = 0.5
MAX_NOTIONAL_PCT = 0.20
MAX_PARTICIPATION_PCT = 0.05
BARS_PER_SESSION = 390

STRONG_BIAS_MULT = 1.0
NORMAL_BIAS_MULT = 0.75
SCORE_MULT_LOW = 0.7
SCORE_MULT_HIGH = 1.0
SCORE_LOW = 50.0
SCORE_HIGH = 100.0
SHORT_SIDE_MULT = 0.75

CONSECUTIVE_STOPOUT_LIMIT = 3
CONSECUTIVE_STOPOUT_HALT_BARS = 24  # 2 hours / 5-minute bars
DAILY_LOSS_LIMIT_PCT = -0.02
WEEKLY_LOSS_LIMIT_PCT = -0.04


def stop_price_long(entry: float, atr_m5: float, stop_atr_mult: float = STOP_ATR_MULT) -> float:
    return entry - stop_atr_mult * atr_m5


def stop_price_short(entry: float, atr_m5: float, stop_atr_mult: float = STOP_ATR_MULT) -> float:
    return entry + stop_atr_mult * atr_m5


def neutral_tighten_stop_long(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float:
    candidate = entry - NEUTRAL_TIGHTEN_ATR_MULT * atr_m5
    if current_price - entry >= NEUTRAL_TIGHTEN_ATR_MULT * atr_m5:
        candidate = max(candidate, entry)
    return max(current_stop, candidate)


def neutral_tighten_stop_short(entry: float, atr_m5: float, current_stop: float, current_price: float) -> float:
    candidate = entry + NEUTRAL_TIGHTEN_ATR_MULT * atr_m5
    if entry - current_price >= NEUTRAL_TIGHTEN_ATR_MULT * atr_m5:
        candidate = min(candidate, entry)
    return min(current_stop, candidate)


def bias_size_multiplier(bias: str) -> float:
    return STRONG_BIAS_MULT if bias in (STRONG_BULL, STRONG_BEAR) else NORMAL_BIAS_MULT


def score_size_multiplier(score: float) -> float:
    frac = (score - SCORE_LOW) / (SCORE_HIGH - SCORE_LOW)
    frac = min(max(frac, 0.0), 1.0)
    return SCORE_MULT_LOW + frac * (SCORE_MULT_HIGH - SCORE_MULT_LOW)


def position_size(
    equity: float,
    risk_per_trade_pct: float,
    stop_distance: float,
    bias: str,
    score: float,
    direction: str,
    short_size_multiplier: float = SHORT_SIDE_MULT,
) -> float:
    """Uncapped share count (07 §2). Caller must apply cap_shares() before use."""
    if stop_distance <= 0:
        return 0.0
    base_shares = (equity * risk_per_trade_pct) / stop_distance
    side_mult = short_size_multiplier if direction == "SHORT" else 1.0
    return base_shares * bias_size_multiplier(bias) * score_size_multiplier(score) * side_mult


def cap_shares(
    shares: float,
    entry_price: float,
    equity: float,
    adv20: float,
    expected_hold_minutes: float,
    max_notional_pct: float = MAX_NOTIONAL_PCT,
    max_participation_pct: float = MAX_PARTICIPATION_PCT,
    bars_per_session: int = BARS_PER_SESSION,
) -> float:
    """07 §2: notional <= max_notional_pct of equity; shares <=
    max_participation_pct of 20-day ADV / bars_per_session * expected_hold_minutes
    (a participation-rate sanity cap, not a literal fill-rate model)."""
    if entry_price <= 0:
        return 0.0
    notional_cap = (equity * max_notional_pct) / entry_price
    participation_cap = (max_participation_pct * adv20 / bars_per_session) * expected_hold_minutes
    capped = min(shares, notional_cap, participation_cap)
    return float(max(capped, 0.0) // 1)


@dataclass
class RiskManager:
    """Account-level guards (07 §1): daily/weekly loss limits and the
    consecutive-stop-out halt. One instance drives the whole backtest; the caller
    (backtest/engine_m5.py) calls new_session()/new_week() on calendar-day/week
    boundaries and register_exit() after every closed trade."""

    starting_equity: float
    starting_equity_today: float = field(init=False)
    starting_equity_week: float = field(init=False)
    consecutive_stopouts_today: int = field(default=0, init=False)
    halt_until_bar: int | None = field(default=None, init=False)
    daily_halted: bool = field(default=False, init=False)
    weekly_halted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.starting_equity_today = self.starting_equity
        self.starting_equity_week = self.starting_equity

    def can_enter(self, bar_index: int) -> bool:
        if self.daily_halted or self.weekly_halted:
            return False
        if self.halt_until_bar is not None and bar_index < self.halt_until_bar:
            return False
        return True

    def register_exit(self, pnl: float, equity: float, exit_reason: str, bar_index: int) -> None:
        if exit_reason == "hard_stop":
            self.consecutive_stopouts_today += 1
            if self.consecutive_stopouts_today >= CONSECUTIVE_STOPOUT_LIMIT:
                self.halt_until_bar = bar_index + CONSECUTIVE_STOPOUT_HALT_BARS
        else:
            self.consecutive_stopouts_today = 0

        if (equity - self.starting_equity_today) / self.starting_equity_today <= DAILY_LOSS_LIMIT_PCT:
            self.daily_halted = True
        if (equity - self.starting_equity_week) / self.starting_equity_week <= WEEKLY_LOSS_LIMIT_PCT:
            self.weekly_halted = True

    def new_session(self, equity: float) -> None:
        self.starting_equity_today = equity
        self.consecutive_stopouts_today = 0
        self.halt_until_bar = None
        self.daily_halted = False

    def new_week(self, equity: float) -> None:
        self.starting_equity_week = equity
        self.weekly_halted = False
