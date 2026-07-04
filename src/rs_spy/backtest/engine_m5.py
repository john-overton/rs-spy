"""M5-cadence event-driven backtest engine. algo-spec/05, 06, 07 (M5-adapted).

Mirrors backtest/engine.py's own two-phase shape (precompute, then a single
chronological event loop) one cadence level up. This file is split into two
halves for reviewability: _prepare_m5 (this task) runs every M5-cadence
indicator/gate/score function once per symbol over its own full history; Part 2
(run_m5_backtest, added in the same file by a later task) drives the bar-by-bar
event loop that consumes PreparedM5's output.

Master calendar = SPY's own M5 bar index for the whole backtest window. Unlike
backtest/engine.py's D1 skeleton (which intersects every symbol's calendar --
fine at D1 density, since daily bars rarely have gaps), M5-cadence coverage
density varies hugely across the curated universe on Alpaca's IEX-only feed (see
IMPLEMENTATION.md's rvol.py deviation -- some symbols have a bar for only ~20% of
RTH minutes). Intersecting 130 symbols' M5 indices at that density would produce
a near-empty calendar. Instead, every symbol's per-bar outputs are computed on
its OWN native M5 index first, then reindexed onto the shared master calendar
(strict reindex, no ffill -- see this plan's Global Constraints section): a
master-calendar bar a thin symbol has no native bar for reads as "no signal"
(NaN/False), which every downstream gate/entry check already treats as "fails",
by construction (NaN comparisons are False in pandas).
"""
from dataclasses import dataclass, field

import pandas as pd

from rs_spy.algo import long as long_algo
from rs_spy.algo import risk
from rs_spy.algo import short as short_algo
from rs_spy.backtest import broker_sim
from rs_spy.bias.buckets import BEAR, BULL, LONG_TRIGGER, NEUTRAL, SHORT_TRIGGER, STRONG_BEAR, STRONG_BULL
from rs_spy.bias.daily_context import daily_context_series
from rs_spy.bias.engine import bias_series
from rs_spy.bias.regime import CHOP, TREND_UP
from rs_spy.data.resample import align_daily_to_intraday
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.selection import gates, scoring, watchlist
from rs_spy.selection.features_m5 import compute_symbol_features_m5

ATR_PERIOD_M5 = 14
EMA8_SPAN = 8
ADV_LOOKBACK_DAYS = 20


@dataclass
class BacktestConfigM5:
    risk_per_trade_pct: float = 0.005
    max_concurrent_long: int = 5
    max_concurrent_short: int = 3
    short_size_multiplier: float = 0.75
    min_list_score: float = 50.0
    min_hold_score: float = 40.0
    top_n_list: int = 20
    top_n_tradeable: int = 5
    max_per_sector: int = 2
    shorts_enabled: bool = False
    starting_equity: float = 100_000.0
    min_adv_shares: float = 50_000.0
    disabled_gates: frozenset = field(default_factory=frozenset)
    # Promoted from the spec default (features_m5.RRS_M5_WINDOW = 12) per the
    # Rounds 2-3 sweep: window=18 is a clean local peak (10 trades / PF 2.06
    # vs 3 / 4.63 at 12; 16 / 0.97 at 24). See docs/tuning/ledger.csv.
    rrs_m5_window: int = 18
    use_qqq_crosscheck: bool = False
    rrs_m5_threshold_long: float = 1.0
    rrs_m5_threshold_short: float = -1.0
    rrs_d1_threshold_long: float = 1.0
    rrs_d1_threshold_short: float = -1.0
    max_entries_per_symbol_long: int = 2
    max_entries_per_symbol_short: int = 1
    expected_hold_minutes: float = 120.0
    unfilled_cancel_bars: int = 2
    stop_atr_mult: float = 1.0
    bias_hold_bars: int = 2  # consecutive family bars required before entries; 1 = current bar only
    confirm_not_extended_atr_mult: float = 1.0
    # Dip-hold (Round 1 alert model): "strict" = current behavior (full gates
    # re-checked every bar); "d1_session" = QUALIFIED persists on D1 gates +
    # hold-score only (intraday gates may fail during the dip; states reset at
    # session open); "grace" = full gates, but tolerate up to
    # dip_hold_grace_bars consecutive failing bars before demotion.
    dip_hold_mode: str = "strict"
    dip_hold_grace_bars: int = 24


@dataclass
class PreparedM5:
    calendar: pd.DatetimeIndex
    bias_df: pd.DataFrame
    regime_d1_m5: pd.Series
    bars: dict
    features: dict
    ema8: dict
    atr_m5: dict
    adv20_m5: dict
    gate_long: dict
    gate_short: dict
    score_long: dict
    score_short: dict
    rs_failure_long: dict
    rs_failure_short: dict
    vwap_loss_long: dict
    vwap_loss_short: dict
    momentum_stall_long: dict
    momentum_stall_short: dict
    confirm_trigger_long: dict
    confirm_trigger_short: dict
    dip_quality_long: dict
    bounce_quality_short: dict
    squeeze_guard_short: dict
    gate_long_hold: dict | None = None
    gate_short_hold: dict | None = None


def _prepare_m5(
    universe_m1: dict,
    universe_m5: dict,
    universe_d1: dict,
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None = None,
    config: BacktestConfigM5 | None = None,
) -> PreparedM5:
    config = config or BacktestConfigM5()
    earnings_blackout = earnings_blackout or {}
    bias_df = bias_series(spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5)
    calendar = bias_df.index

    daily_ctx = daily_context_series(spy_d1)
    regime_d1_m5 = align_daily_to_intraday(daily_ctx["regime_d1"], calendar)

    bars, features, ema8, atr_m5, adv20_m5 = {}, {}, {}, {}, {}
    gate_long, gate_short, score_long, score_short = {}, {}, {}, {}
    gate_long_hold, gate_short_hold = {}, {}
    rs_failure_long, rs_failure_short = {}, {}
    vwap_loss_long, vwap_loss_short = {}, {}
    momentum_stall_long, momentum_stall_short = {}, {}
    confirm_trigger_long, confirm_trigger_short = {}, {}
    dip_quality_long, bounce_quality_short, squeeze_guard_short = {}, {}, {}

    for sym, df_m5_native in universe_m5.items():
        df_m1_native = universe_m1[sym]
        df_d1_native = universe_d1[sym]

        feat_native = compute_symbol_features_m5(
            df_m1_native, df_m5_native, df_d1_native, spy_m1, spy_m5, spy_d1,
            qqq_m5=qqq_m5 if config.use_qqq_crosscheck else None,
            rrs_window=config.rrs_m5_window,
        )
        atr_native = atr_fn(df_m5_native, n=ATR_PERIOD_M5)
        ema8_native = df_m5_native["close"].ewm(span=EMA8_SPAN, adjust=False).mean()
        adv20_daily = df_d1_native["volume"].rolling(ADV_LOOKBACK_DAYS).mean()
        adv20_native = align_daily_to_intraday(adv20_daily, df_m5_native.index)

        gl_native = gates.gates_pass_long_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_long,
            rrs_d1_threshold=config.rrs_d1_threshold_long,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
            adv20=adv20_native,
        ).fillna(False)
        gs_native = gates.gates_pass_short_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_short,
            rrs_d1_threshold=config.rrs_d1_threshold_short,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
            adv20=adv20_native,
        ).fillna(False)
        hold_disabled = frozenset(config.disabled_gates) | {"rrs_m5", "vwap", "one_candle_wonder"}
        gl_hold_native = gates.gates_pass_long_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_long,
            rrs_d1_threshold=config.rrs_d1_threshold_long,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=hold_disabled,
            adv20=adv20_native,
        ).fillna(False)
        gs_hold_native = gates.gates_pass_short_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_m5_threshold=config.rrs_m5_threshold_short,
            rrs_d1_threshold=config.rrs_d1_threshold_short,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=hold_disabled,
            adv20=adv20_native,
        ).fillna(False)
        sl_native = scoring.score_long_m5(feat_native)
        ss_native = scoring.score_short_m5(feat_native)

        rs_fail_l_native = long_algo.rs_failure_long(feat_native["rolling_rrs_m5"])
        rs_fail_s_native = short_algo.rs_failure_short(feat_native["rolling_rrs_m5"])
        vwap_l_native = long_algo.vwap_loss_long(feat_native["close"], feat_native["vwap_m5"])
        vwap_s_native = short_algo.vwap_loss_short(feat_native["close"], feat_native["vwap_m5"])
        stall_l_native = long_algo.momentum_stall_long(feat_native["lrsi_m5"])
        stall_s_native = short_algo.momentum_stall_short(feat_native["lrsi_m5"])
        confirm_l_native = long_algo.confirm_trigger_entry_long(
            feat_native, ema8_native, atr_native,
            rrs_m5_threshold=config.rrs_m5_threshold_long,
            not_extended_atr_mult=config.confirm_not_extended_atr_mult,
        )
        confirm_s_native = short_algo.confirm_trigger_entry_short(
            feat_native, ema8_native, atr_native,
            rrs_m5_threshold=config.rrs_m5_threshold_short,
            not_extended_atr_mult=config.confirm_not_extended_atr_mult,
        )
        dip_l_native = long_algo.dip_quality_pass_long(df_m5_native, feat_native, atr_native)
        bounce_s_native = short_algo.bounce_quality_pass_short(df_m5_native, feat_native, atr_native)
        squeeze_s_native = short_algo.squeeze_guard_short(
            df_m5_native["high"], df_m5_native["close"].shift(1), atr_native, feat_native["rvol_m5"]
        )

        bars[sym] = df_m5_native.reindex(calendar)
        features[sym] = feat_native.reindex(calendar)
        ema8[sym] = ema8_native.reindex(calendar)
        atr_m5[sym] = atr_native.reindex(calendar)
        adv20_m5[sym] = adv20_native.reindex(calendar)
        gate_long[sym] = gl_native.reindex(calendar, fill_value=False)
        gate_short[sym] = gs_native.reindex(calendar, fill_value=False)
        gate_long_hold[sym] = gl_hold_native.reindex(calendar, fill_value=False)
        gate_short_hold[sym] = gs_hold_native.reindex(calendar, fill_value=False)
        score_long[sym] = sl_native.reindex(calendar)
        score_short[sym] = ss_native.reindex(calendar)
        rs_failure_long[sym] = rs_fail_l_native.reindex(calendar, fill_value=False)
        rs_failure_short[sym] = rs_fail_s_native.reindex(calendar, fill_value=False)
        vwap_loss_long[sym] = vwap_l_native.reindex(calendar, fill_value=False)
        vwap_loss_short[sym] = vwap_s_native.reindex(calendar, fill_value=False)
        momentum_stall_long[sym] = stall_l_native.reindex(calendar, fill_value=False)
        momentum_stall_short[sym] = stall_s_native.reindex(calendar, fill_value=False)
        confirm_trigger_long[sym] = confirm_l_native.reindex(calendar, fill_value=False)
        confirm_trigger_short[sym] = confirm_s_native.reindex(calendar, fill_value=False)
        dip_quality_long[sym] = dip_l_native.reindex(calendar, fill_value=False)
        bounce_quality_short[sym] = bounce_s_native.reindex(calendar, fill_value=False)
        squeeze_guard_short[sym] = squeeze_s_native.reindex(calendar, fill_value=False)

    return PreparedM5(
        calendar=calendar,
        bias_df=bias_df,
        regime_d1_m5=regime_d1_m5,
        bars=bars,
        features=features,
        ema8=ema8,
        atr_m5=atr_m5,
        adv20_m5=adv20_m5,
        gate_long=gate_long,
        gate_short=gate_short,
        score_long=score_long,
        score_short=score_short,
        rs_failure_long=rs_failure_long,
        rs_failure_short=rs_failure_short,
        vwap_loss_long=vwap_loss_long,
        vwap_loss_short=vwap_loss_short,
        momentum_stall_long=momentum_stall_long,
        momentum_stall_short=momentum_stall_short,
        confirm_trigger_long=confirm_trigger_long,
        confirm_trigger_short=confirm_trigger_short,
        dip_quality_long=dip_quality_long,
        bounce_quality_short=bounce_quality_short,
        squeeze_guard_short=squeeze_guard_short,
        gate_long_hold=gate_long_hold,
        gate_short_hold=gate_short_hold,
    )


# --- Part 2: run_m5_backtest event loop -------------------------------------
#
# Consumes PreparedM5 (above) and drives a single chronological bar-by-bar
# loop over the master calendar: fill pending entries, manage/close open
# positions, update watchlist state, then submit new entries. Mirrors
# backtest/engine.py's run_d1_backtest shape one cadence level up.

LONG = "LONG"
SHORT = "SHORT"

NEW_ENTRY_CUTOFF = pd.Timedelta(hours=15, minutes=30)
TIME_FLAT = pd.Timedelta(hours=15, minutes=55)
FINAL_STRETCH_START = pd.Timedelta(hours=15, minutes=30)
FINAL_STRETCH_TARGET_MULT = 0.75


def _et_time_of_day(index: pd.DatetimeIndex) -> pd.Series:
    et = index.tz_convert("America/New_York")
    return pd.Series(et - et.normalize(), index=index)


@dataclass
class PositionM5:
    symbol: str
    direction: str
    entry_bar: int
    entry_time: pd.Timestamp
    entry_price: float
    shares: float
    stop: float
    entry_atr: float
    peak_favorable: float = 0.0
    trail_engaged: bool = False


@dataclass
class TradeM5:
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    shares: float
    exit_reason: str
    pnl: float
    r_multiple: float


@dataclass
class BacktestResultM5:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series | None = None
    funnel: dict = field(default_factory=dict)

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([vars(t) for t in self.trades])


def run_m5_backtest(
    universe_m1: dict,
    universe_m5: dict,
    universe_d1: dict,
    spy_m1: pd.DataFrame,
    spy_m5: pd.DataFrame,
    spy_d1: pd.DataFrame,
    qqq_m1: pd.DataFrame,
    qqq_m5: pd.DataFrame,
    sectors: dict,
    earnings_blackout: dict | None = None,
    config: BacktestConfigM5 | None = None,
    prepared: PreparedM5 | None = None,
) -> BacktestResultM5:
    """Bar-by-bar M5 event loop. When `prepared` is given, the ~15-20 minute
    _prepare_m5 precompute is skipped and the caller guarantees it was built
    from the SAME universe/data arguments with a config whose prepare-baked
    fields match. Safe to vary against a shared `prepared` (event-loop-only):
    risk_per_trade_pct, max_concurrent_*, short_size_multiplier,
    min_list_score, min_hold_score, top_n_*, max_per_sector, shorts_enabled,
    starting_equity, stop_atr_mult, max_entries_per_symbol_*,
    expected_hold_minutes, unfilled_cancel_bars, bias_hold_bars, disabled_gates only for
    "bias", and dip_hold_mode/dip_hold_grace_bars (the hold-gate series are always
    computed in _prepare_m5 regardless of mode, so any freshly-built PreparedM5
    supports switching dip_hold_mode without a rebuild). Baked into `prepared`
    (need a fresh _prepare_m5 to vary): min_adv_shares, non-bias disabled_gates
    entries, rrs_m5_window, use_qqq_crosscheck, confirm_not_extended_atr_mult,
    and the four rrs_*_threshold_* fields (the latter also feed the trigger-bar
    reconfirmation checks, not just the initial gates)."""
    config = config or BacktestConfigM5()
    if prepared is None:
        prepared = _prepare_m5(
            universe_m1, universe_m5, universe_d1, spy_m1, spy_m5, spy_d1, qqq_m1, qqq_m5,
            sectors, earnings_blackout, config,
        )
    calendar = prepared.calendar
    et_tod = _et_time_of_day(calendar)
    sessions = calendar.normalize()
    weeks = calendar.isocalendar().week.to_numpy()

    if "bias" in config.disabled_gates:
        bias_ok_long = pd.Series(True, index=calendar)
        bias_ok_short = pd.Series(True, index=calendar)
    else:
        bias_ok_long_family = prepared.bias_df["bias"].isin([BULL, STRONG_BULL])
        bias_ok_long = bias_ok_long_family.copy()
        for k in range(1, config.bias_hold_bars):
            bias_ok_long &= bias_ok_long_family.shift(k, fill_value=False)
        bias_ok_short_family = prepared.bias_df["bias"].isin([BEAR, STRONG_BEAR])
        bias_ok_short = bias_ok_short_family.copy()
        for k in range(1, config.bias_hold_bars):
            bias_ok_short &= bias_ok_short_family.shift(k, fill_value=False)
        bias_ok_short &= prepared.regime_d1_m5 != TREND_UP
    in_entry_window = (~prepared.bias_df["warmup"]) & (et_tod <= NEW_ENTRY_CUTOFF)

    # Cross-detection "previous" values (dip-arm RRS/LRSI crossings) must be
    # each symbol's own last REAL reading, not whatever sat on the immediately
    # preceding master-calendar row -- which is NaN for thin/gappy symbols and
    # silently suppressed the crossing (IMPLEMENTATION.md known-limitation #23,
    # fixed here). ffill().shift(1) on the reindexed frame reproduces the
    # native shift(1) at every bar the symbol actually trades: the forward-fill
    # carries the last real reading across gap rows, and the shift moves it
    # strictly before the current bar. Bars the symbol has no native data for
    # still can't arm (rrs_now itself is NaN there, and NaN comparisons are
    # False), matching the strict-reindex "no signal" convention.
    rrs_prev_by_sym = {sym: prepared.features[sym]["rolling_rrs_m5"].ffill().shift(1) for sym in universe_m5}
    lrsi_prev_by_sym = {sym: prepared.features[sym]["lrsi_m5"].ffill().shift(1) for sym in universe_m5}

    if config.dip_hold_mode not in ("strict", "d1_session", "grace"):
        raise ValueError(f"unknown dip_hold_mode: {config.dip_hold_mode!r}")
    if config.dip_hold_mode == "d1_session" and prepared.gate_long_hold is None:
        raise ValueError("dip_hold_mode='d1_session' needs a PreparedM5 with gate_long_hold/gate_short_hold")
    gate_fail_streak_long = dict.fromkeys(universe_m5, 0)
    gate_fail_streak_short = dict.fromkeys(universe_m5, 0)

    state_long = dict.fromkeys(universe_m5, watchlist.IDLE)
    state_short = dict.fromkeys(universe_m5, watchlist.IDLE)
    entry_path_long: dict = {}
    entry_path_short: dict = {}
    positions: dict = {}
    pending: dict = {}  # symbol -> broker_sim pending-entry dict
    entries_today_long: dict = {}
    entries_today_short: dict = {}
    locked_out_long: set = set()
    locked_out_short: set = set()

    # Entry-funnel instrumentation (tuning-matrix cell D3): flat event counters
    # over the whole run. qualified_signals = IDLE->QUALIFIED transitions;
    # trigger_coincidences = (trigger bar x QUALIFIED symbol) pairs counted
    # BEFORE the bias 2-bar-hold check; eval_blocked_* = ENTRY_EVAL symbol-bars
    # turned away by that bar-level condition (re-arming counts again -- the
    # funnel measures opportunities, not unique symbols). Short-side trigger/
    # eval counters only accumulate when shorts_enabled=True. In every mode,
    # trigger_coincidences == trigger_killed_by_bias_hold + trigger_killed_by_gate
    # + trigger_bypass: a coincidence either fails the bias hold, fails the full
    # gate (only reachable in the d1_session/grace dip-hold modes, where a
    # symbol can be QUALIFIED while its full gate is False), or bypasses.
    funnel = {
        f"{side}_{key}": 0
        for side in ("long", "short")
        for key in (
            "qualified_signals", "dip_armed", "entry_eval_via_dip",
            "trigger_bars", "trigger_coincidences", "trigger_killed_by_bias_hold",
            "trigger_killed_by_gate", "trigger_bypass",
            "eval_blocked_no_entry_window", "eval_blocked_risk_halt", "eval_blocked_bias",
            "eval_killed_by_lockout_or_cap", "eval_killed_by_quality", "eval_killed_by_ranking",
            "eval_killed_by_slots", "eval_killed_by_sizing",
            "orders_submitted", "orders_filled", "orders_cancelled_unfilled",
        )
    }

    risk_mgr = risk.RiskManager(starting_equity=config.starting_equity)
    equity = config.starting_equity
    equity_curve = []
    trades: list[TradeM5] = []

    prev_session = None
    prev_week = None

    for i, ts in enumerate(calendar):
        session = sessions[i]
        week = weeks[i]
        if session != prev_session:
            entries_today_long = {}
            entries_today_short = {}
            locked_out_long = set()
            locked_out_short = set()
            risk_mgr.new_session(equity)
            if config.dip_hold_mode == "d1_session":
                state_long = dict.fromkeys(universe_m5, watchlist.IDLE)
                state_short = dict.fromkeys(universe_m5, watchlist.IDLE)
            prev_session = session
        if week != prev_week:
            risk_mgr.new_week(equity)
            prev_week = week

        bias_now = prepared.bias_df["bias"].iat[i]
        flip_now = prepared.bias_df["flip_flatten"].iat[i]
        regime_now = prepared.regime_d1_m5.iat[i]
        time_now = et_tod.iat[i]

        # 1. try to fill pending entries (bar AFTER the signal bar)
        for sym, order in list(pending.items()):
            bar = prepared.bars[sym].iloc[i]
            if pd.isna(bar["open"]):
                order["bars_waited"] += 1
            else:
                fill = broker_sim.try_fill_entry(order["direction"], order["limit_price"], bar["open"], bar["high"], bar["low"])
                if fill is not None:
                    fill = broker_sim.apply_slippage(fill, order["direction"], is_entry=True)
                    positions[sym] = PositionM5(
                        symbol=sym, direction=order["direction"], entry_bar=i, entry_time=ts,
                        entry_price=fill, shares=order["shares"], stop=order["stop"], entry_atr=order["atr"],
                    )
                    funnel[("long_" if order["direction"] == LONG else "short_") + "orders_filled"] += 1
                    book = entries_today_long if order["direction"] == LONG else entries_today_short
                    book[sym] = book.get(sym, 0) + 1
                    del pending[sym]
                    continue
                order["bars_waited"] += 1
            if order["bars_waited"] >= config.unfilled_cancel_bars:
                funnel[("long_" if order["direction"] == LONG else "short_") + "orders_cancelled_unfilled"] += 1
                del pending[sym]

        # 2. manage open positions
        to_close = []
        for sym, pos in positions.items():
            bar = prepared.bars[sym].iloc[i]
            if pd.isna(bar["close"]):
                continue  # no fresh bar for this symbol -- carry the position forward unmanaged this bar
            atr = prepared.atr_m5[sym].iat[i]

            if pos.direction == LONG:
                if bar["low"] <= pos.stop:
                    to_close.append((sym, min(pos.stop, bar["open"]), "trail_stop" if pos.trail_engaged else "hard_stop"))
                    continue
                if bool(flip_now) and bias_now in (BEAR, STRONG_BEAR):
                    to_close.append((sym, bar["close"], "market_flip"))
                    continue
                if prepared.rs_failure_long[sym].iat[i]:
                    to_close.append((sym, bar["close"], "rs_failure"))
                    continue
                if prepared.vwap_loss_long[sym].iat[i]:
                    to_close.append((sym, bar["close"], "vwap_loss"))
                    continue
                favorable = bar["close"] - pos.entry_price
                pos.peak_favorable = max(pos.peak_favorable, favorable)
                target_mult = long_algo.PROFIT_TARGET_ATR_MULT
                if regime_now == CHOP:
                    target_mult *= long_algo.CHOP_PROFIT_TARGET_MULT
                if time_now >= FINAL_STRETCH_START:
                    target_mult *= FINAL_STRETCH_TARGET_MULT
                if prepared.momentum_stall_long[sym].iat[i] and favorable >= target_mult * pos.entry_atr:
                    to_close.append((sym, bar["close"], "profit_take"))
                    continue
                if bias_now == NEUTRAL and not pd.isna(atr):
                    pos.stop = risk.neutral_tighten_stop_long(pos.entry_price, atr, pos.stop, bar["close"])
                if pos.peak_favorable >= long_algo.TRAIL_TRIGGER_ATR_MULT * pos.entry_atr and not pd.isna(atr):
                    # trail_engaged marks the trigger having ever fired -- a later
                    # stop exit is a "trail_stop" (managed exit), not a stop-OUT,
                    # so it must not feed the lockout/consecutive-stop-out logic.
                    pos.trail_engaged = True
                    e8 = prepared.ema8[sym].iat[i]
                    trail = e8 - long_algo.TRAIL_STOP_ATR_MULT * atr
                    pos.stop = max(pos.stop, max(trail, pos.entry_price))
                if time_now >= TIME_FLAT:
                    to_close.append((sym, bar["close"], "time_flat"))
                    continue
            else:  # SHORT
                if bar["high"] >= pos.stop:
                    to_close.append((sym, max(pos.stop, bar["open"]), "trail_stop" if pos.trail_engaged else "hard_stop"))
                    continue
                if prepared.squeeze_guard_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "squeeze_guard"))
                    continue
                if bias_now in (BULL, STRONG_BULL):
                    to_close.append((sym, bar["close"], "market_flip"))
                    continue
                if prepared.rs_failure_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "rs_failure"))
                    continue
                if prepared.vwap_loss_short[sym].iat[i]:
                    to_close.append((sym, bar["close"], "vwap_loss"))
                    continue
                favorable = pos.entry_price - bar["close"]
                pos.peak_favorable = max(pos.peak_favorable, favorable)
                target_mult = short_algo.PROFIT_TARGET_ATR_MULT
                if regime_now == CHOP:
                    target_mult *= short_algo.CHOP_PROFIT_TARGET_MULT
                if time_now >= FINAL_STRETCH_START:
                    target_mult *= FINAL_STRETCH_TARGET_MULT
                if prepared.momentum_stall_short[sym].iat[i] and favorable >= target_mult * pos.entry_atr:
                    to_close.append((sym, bar["close"], "profit_take"))
                    continue
                if bias_now == NEUTRAL and not pd.isna(atr):
                    pos.stop = risk.neutral_tighten_stop_short(pos.entry_price, atr, pos.stop, bar["close"])
                if pos.peak_favorable >= short_algo.TRAIL_TRIGGER_ATR_MULT * pos.entry_atr and not pd.isna(atr):
                    # trail_engaged marks the trigger having ever fired -- a later
                    # stop exit is a "trail_stop" (managed exit), not a stop-OUT,
                    # so it must not feed the lockout/consecutive-stop-out logic.
                    pos.trail_engaged = True
                    e8 = prepared.ema8[sym].iat[i]
                    trail = e8 + short_algo.TRAIL_STOP_ATR_MULT * atr
                    pos.stop = min(pos.stop, min(trail, pos.entry_price))
                if time_now >= TIME_FLAT:
                    to_close.append((sym, bar["close"], "time_flat"))
                    continue

        for sym, exit_price, reason in to_close:
            pos = positions.pop(sym)
            exit_price = broker_sim.apply_slippage(exit_price, pos.direction, is_entry=False)
            pnl_per_share = (exit_price - pos.entry_price) if pos.direction == LONG else (pos.entry_price - exit_price)
            pnl = pnl_per_share * pos.shares
            stop_dist = abs(pos.entry_price - pos.stop) or pos.entry_atr or 1.0
            r_multiple = pnl_per_share / stop_dist
            equity += pnl
            trades.append(
                TradeM5(
                    symbol=sym, direction=pos.direction, entry_time=pos.entry_time, entry_price=pos.entry_price,
                    exit_time=ts, exit_price=exit_price, shares=pos.shares, exit_reason=reason, pnl=pnl,
                    r_multiple=r_multiple,
                )
            )
            if reason == "hard_stop":
                (locked_out_long if pos.direction == LONG else locked_out_short).add(sym)
            risk_mgr.register_exit(pnl, equity, reason, i)

        equity_curve.append(equity)

        # 3. update watchlist state (long book)
        can_enter_now = risk_mgr.can_enter(i) and in_entry_window.iat[i]
        for sym in universe_m5:
            gl = bool(prepared.gate_long[sym].iat[i])
            score = prepared.score_long[sym].iat[i]
            rrs_now = prepared.features[sym]["rolling_rrs_m5"].iat[i]
            rrs_prev = rrs_prev_by_sym[sym].iat[i]
            lrsi_now = prepared.features[sym]["lrsi_m5"].iat[i]
            lrsi_prev = lrsi_prev_by_sym[sym].iat[i]
            prev_state = state_long[sym]
            if config.dip_hold_mode == "d1_session":
                hold_l = bool(prepared.gate_long_hold[sym].iat[i])
            elif config.dip_hold_mode == "grace":
                gate_fail_streak_long[sym] = 0 if gl else gate_fail_streak_long[sym] + 1
                hold_l = gl or gate_fail_streak_long[sym] <= config.dip_hold_grace_bars
            else:
                hold_l = None
            state_long[sym] = watchlist.next_state_long(
                prev_state, gl, score, rrs_prev, rrs_now,
                lrsi_prev=lrsi_prev, lrsi_now=lrsi_now,
                min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
                hold_gate_pass=hold_l,
            )
            if prev_state == watchlist.IDLE and state_long[sym] == watchlist.QUALIFIED:
                funnel["long_qualified_signals"] += 1
            if prev_state == watchlist.QUALIFIED and state_long[sym] == watchlist.DIP_ARMED:
                entry_path_long[sym] = "B"
                funnel["long_dip_armed"] += 1
            elif prev_state == watchlist.DIP_ARMED and state_long[sym] == watchlist.ENTRY_EVAL:
                funnel["long_entry_eval_via_dip"] += 1  # entry_path_long[sym] already "B" from the prior bar
            if config.shorts_enabled:
                gs = bool(prepared.gate_short[sym].iat[i])
                score_s = prepared.score_short[sym].iat[i]
                prev_state_s = state_short[sym]
                if config.dip_hold_mode == "d1_session":
                    hold_s = bool(prepared.gate_short_hold[sym].iat[i])
                elif config.dip_hold_mode == "grace":
                    gate_fail_streak_short[sym] = 0 if gs else gate_fail_streak_short[sym] + 1
                    hold_s = gs or gate_fail_streak_short[sym] <= config.dip_hold_grace_bars
                else:
                    hold_s = None
                state_short[sym] = watchlist.next_state_short(
                    prev_state_s, gs, score_s, rrs_prev, rrs_now,
                    lrsi_prev=lrsi_prev, lrsi_now=lrsi_now,
                    min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
                    hold_gate_pass=hold_s,
                )
                if prev_state_s == watchlist.IDLE and state_short[sym] == watchlist.QUALIFIED:
                    funnel["short_qualified_signals"] += 1
                if prev_state_s == watchlist.QUALIFIED and state_short[sym] == watchlist.DIP_ARMED:
                    entry_path_short[sym] = "B"
                    funnel["short_dip_armed"] += 1
                elif prev_state_s == watchlist.DIP_ARMED and state_short[sym] == watchlist.ENTRY_EVAL:
                    funnel["short_entry_eval_via_dip"] += 1

        trigger_now = prepared.bias_df["trigger"].iat[i]
        if trigger_now == LONG_TRIGGER:
            funnel["long_trigger_bars"] += 1
            bias_ok_now = bool(bias_ok_long.iat[i])
            for sym in universe_m5:
                if state_long[sym] != watchlist.QUALIFIED:
                    continue
                funnel["long_trigger_coincidences"] += 1
                if not bias_ok_now:
                    funnel["long_trigger_killed_by_bias_hold"] += 1
                    continue
                gl = bool(prepared.gate_long[sym].iat[i])
                new_state = watchlist.apply_trigger_bypass(state_long[sym], gl, True)
                if new_state == state_long[sym]:
                    funnel["long_trigger_killed_by_gate"] += 1
                else:
                    state_long[sym] = new_state
                    entry_path_long[sym] = "A"
                    funnel["long_trigger_bypass"] += 1
        if config.shorts_enabled and trigger_now == SHORT_TRIGGER:
            funnel["short_trigger_bars"] += 1
            bias_ok_now_s = bool(bias_ok_short.iat[i])
            for sym in universe_m5:
                if state_short[sym] != watchlist.QUALIFIED:
                    continue
                funnel["short_trigger_coincidences"] += 1
                if not bias_ok_now_s:
                    funnel["short_trigger_killed_by_bias_hold"] += 1
                    continue
                gs = bool(prepared.gate_short[sym].iat[i])
                new_state = watchlist.apply_trigger_bypass(state_short[sym], gs, True)
                if new_state == state_short[sym]:
                    funnel["short_trigger_killed_by_gate"] += 1
                else:
                    state_short[sym] = new_state
                    entry_path_short[sym] = "A"
                    funnel["short_trigger_bypass"] += 1

        # 4. submit entries for symbols now in ENTRY_EVAL
        evals_long = [
            sym for sym in universe_m5
            if state_long[sym] == watchlist.ENTRY_EVAL and sym not in positions and sym not in pending
        ]
        if evals_long and not in_entry_window.iat[i]:
            funnel["long_eval_blocked_no_entry_window"] += len(evals_long)
        elif evals_long and not risk_mgr.can_enter(i):
            funnel["long_eval_blocked_risk_halt"] += len(evals_long)
        elif evals_long and not bias_ok_long.iat[i]:
            funnel["long_eval_blocked_bias"] += len(evals_long)
        if can_enter_now and bias_ok_long.iat[i]:
            eligible = {}
            for sym in evals_long:
                if sym in locked_out_long or entries_today_long.get(sym, 0) >= config.max_entries_per_symbol_long:
                    funnel["long_eval_killed_by_lockout_or_cap"] += 1
                    continue
                path = entry_path_long.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_long[sym].iat[i] if path == "A" else prepared.dip_quality_long[sym].iat[i]
                )
                if qualifies:
                    eligible[sym] = prepared.score_long[sym].iat[i]
                else:
                    funnel["long_eval_killed_by_quality"] += 1
            tradeable = watchlist.build_tradeable_list(
                eligible, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            funnel["long_eval_killed_by_ranking"] += len(eligible) - len(tradeable)
            slots_free = (
                config.max_concurrent_long
                - sum(1 for p in positions.values() if p.direction == LONG)
                - sum(1 for o in pending.values() if o["direction"] == LONG)
            )
            funnel["long_eval_killed_by_slots"] += max(0, len(tradeable) - max(0, slots_free))
            for sym in tradeable[:slots_free]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    funnel["long_eval_killed_by_sizing"] += 1
                    continue
                stop = risk.stop_price_long(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
                stop_dist = bar["close"] - stop
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_long[sym].iat[i], LONG,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    funnel["long_eval_killed_by_sizing"] += 1
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, LONG)
                pending[sym] = {"direction": LONG, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}
                funnel["long_orders_submitted"] += 1

        evals_short = [
            sym for sym in universe_m5
            if state_short[sym] == watchlist.ENTRY_EVAL and sym not in positions and sym not in pending
        ] if config.shorts_enabled else []
        if evals_short and not in_entry_window.iat[i]:
            funnel["short_eval_blocked_no_entry_window"] += len(evals_short)
        elif evals_short and not risk_mgr.can_enter(i):
            funnel["short_eval_blocked_risk_halt"] += len(evals_short)
        elif evals_short and not bias_ok_short.iat[i]:
            funnel["short_eval_blocked_bias"] += len(evals_short)
        if config.shorts_enabled and can_enter_now and bias_ok_short.iat[i]:
            eligible_s = {}
            for sym in evals_short:
                if sym in locked_out_short or entries_today_short.get(sym, 0) >= config.max_entries_per_symbol_short:
                    funnel["short_eval_killed_by_lockout_or_cap"] += 1
                    continue
                path = entry_path_short.get(sym, "B")
                qualifies = (
                    prepared.confirm_trigger_short[sym].iat[i] if path == "A" else prepared.bounce_quality_short[sym].iat[i]
                )
                if qualifies:
                    eligible_s[sym] = prepared.score_short[sym].iat[i]
                else:
                    funnel["short_eval_killed_by_quality"] += 1
            tradeable_s = watchlist.build_tradeable_list(
                eligible_s, sectors, config.min_list_score, config.top_n_list, config.top_n_tradeable, config.max_per_sector,
            )
            funnel["short_eval_killed_by_ranking"] += len(eligible_s) - len(tradeable_s)
            slots_free_s = (
                config.max_concurrent_short
                - sum(1 for p in positions.values() if p.direction == SHORT)
                - sum(1 for o in pending.values() if o["direction"] == SHORT)
            )
            funnel["short_eval_killed_by_slots"] += max(0, len(tradeable_s) - max(0, slots_free_s))
            for sym in tradeable_s[:slots_free_s]:
                bar = prepared.bars[sym].iloc[i]
                atr = prepared.atr_m5[sym].iat[i]
                if pd.isna(bar["close"]) or pd.isna(atr) or atr <= 0:
                    funnel["short_eval_killed_by_sizing"] += 1
                    continue
                stop = risk.stop_price_short(bar["close"], atr, stop_atr_mult=config.stop_atr_mult)
                stop_dist = stop - bar["close"]
                shares = risk.position_size(
                    equity, config.risk_per_trade_pct, stop_dist, bias_now, prepared.score_short[sym].iat[i], SHORT,
                    short_size_multiplier=config.short_size_multiplier,
                )
                shares = risk.cap_shares(
                    shares, bar["close"], equity, prepared.adv20_m5[sym].iat[i], config.expected_hold_minutes,
                )
                if shares <= 0:
                    funnel["short_eval_killed_by_sizing"] += 1
                    continue
                limit = broker_sim.entry_limit_price(bar["close"], atr, SHORT)
                pending[sym] = {"direction": SHORT, "limit_price": limit, "stop": stop, "atr": atr, "shares": shares, "bars_waited": 0}
                funnel["short_orders_submitted"] += 1

    equity_series = pd.Series(equity_curve, index=calendar)
    return BacktestResultM5(trades=trades, equity_curve=equity_series, funnel=funnel)
