"""D1 walking-skeleton backtest engine. algo-spec/05,06,07 (D1-adapted).

Timing model: all signals (bias, gates, scores, watchlist transitions) are
computed from information available as of day t's close; any resulting
order fills at day t+1's open. This is the standard no-lookahead convention
for daily-bar backtests and matches the "signals close-only, fills next
bar" philosophy in 08 §1.

Position management order per open position (mirrors 05 §4 / 06 §4,
D1-adapted -- see module docstrings in bias/engine_d1.py and
selection/watchlist.py for what's approximated):
  1. hard stop (intraday low/high through the stop level)
  2. bias flip against the position, with SPY stacked-red/green confirmation
  3. RRS failure (rolling D1 RRS turns against the position for 2 days)
  4. profit-take (target gain reached AND HA continuation stalls)
  5. trailing stop (EMA8-D1 based, engaged after a threshold gain)
  6. max hold (a pragmatic cap not in the spec, since D1 has no explicit
     "time flat" rule -- prevents unbounded swing holds)
"""
from dataclasses import dataclass, field

import pandas as pd

from rs_spy.bias.engine_d1 import BEAR, BULL, LONG_TRIGGER, STRONG_BEAR, STRONG_BULL
from rs_spy.indicators.candle_structure import stacked_count
from rs_spy.selection import gates, scoring, watchlist
from rs_spy.selection.features import RRS_D1_WINDOW, compute_symbol_features

LONG = "LONG"
SHORT = "SHORT"

STOP_ATR_MULT = 1.5
TRAIL_TRIGGER_ATR_MULT = 1.5
TRAIL_STOP_ATR_MULT = 0.25
PROFIT_TARGET_ATR_MULT = 1.5
MAX_HOLD_DAYS = 40
EMA8_SPAN = 8


@dataclass
class Position:
    symbol: str
    direction: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: float
    stop: float
    entry_atr: float
    days_held: int = 0
    peak_favorable: float = 0.0


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    shares: float
    exit_reason: str
    pnl: float
    r_multiple: float


@dataclass
class BacktestConfig:
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
    # algo-spec/01's ADV gate (1,000,000 shares) is calibrated for full-market
    # (SIP) consolidated volume. Our free-tier Alpaca feed is IEX-only, which
    # carries roughly 2-3% of consolidated share volume -- confirmed against
    # this universe's actual cached data (e.g. median IEX volume for GS/HON
    # is ~50-75k/day despite both being genuinely deep, liquid mega-caps).
    # Recalibrated down accordingly; RVOL-style *relative* volume gates are
    # unaffected since they compare the same feed to its own rolling average.
    min_adv_shares: float = 50_000.0
    # M3.5 study knobs (algo-spec 08 §3.1/§3.3) -- defaults reproduce the M3
    # baseline exactly. `disabled_gates` is a subset of
    # selection.gates.HARD_RULE_NAMES ({"bias","rrs","ha","sma"}); "bias"
    # bypasses the outer bias-tier filter below, the rest pass through to
    # gates_pass_long/short. `rrs_window` overrides features.RRS_D1_WINDOW.
    # `rrs_use_rolling=False` gates on raw per-bar RRS instead of the rolling
    # mean. `rrs_threshold_long`/`rrs_threshold_short` override the gate
    # thresholds used by gates_pass_long/short.
    disabled_gates: frozenset = field(default_factory=frozenset)
    rrs_window: int = RRS_D1_WINDOW
    rrs_use_rolling: bool = True
    rrs_threshold_long: float = 1.0
    rrs_threshold_short: float = -1.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series | None = None

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([vars(t) for t in self.trades])


def _align_calendar(spy: pd.DataFrame, bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx = spy.index
    for df in bars_by_symbol.values():
        idx = idx.intersection(df.index)
    return idx.sort_values()


def _prepare(
    calendar: pd.DatetimeIndex,
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    bars_by_symbol: dict[str, pd.DataFrame],
    rrs_window: int = RRS_D1_WINDOW,
):
    spy = spy.loc[calendar]
    qqq = qqq.loc[calendar]
    features = {}
    scores_long = {}
    scores_short = {}
    ema8 = {}
    for sym, df in bars_by_symbol.items():
        df = df.loc[calendar]
        feat = compute_symbol_features(df, spy, rrs_window=rrs_window)
        features[sym] = feat
        scores_long[sym] = scoring.score_long(feat)
        scores_short[sym] = scoring.score_short(feat)
        ema8[sym] = df["close"].ewm(span=EMA8_SPAN, adjust=False).mean()
    return spy, qqq, features, scores_long, scores_short, ema8


def run_d1_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    sectors: dict[str, str],
    earnings_blackout: dict[str, set] | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    from rs_spy.bias.engine_d1 import bias_series_d1

    config = config or BacktestConfig()
    earnings_blackout = earnings_blackout or {}
    calendar = _align_calendar(spy, bars_by_symbol)
    spy, qqq, features, scores_long, scores_short, ema8 = _prepare(
        calendar, spy, qqq, bars_by_symbol, rrs_window=config.rrs_window
    )
    bias_df = bias_series_d1(spy, qqq)

    rrs_column = "rolling_rrs_d1" if config.rrs_use_rolling else "rrs_d1"
    gate_long = {}
    gate_short = {}
    for sym, df in bars_by_symbol.items():
        df = df.loc[calendar]
        gate_long[sym] = gates.gates_pass_long(
            df,
            features[sym],
            earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_threshold=config.rrs_threshold_long,
            rrs_column=rrs_column,
            disabled=config.disabled_gates,
        )
        gate_short[sym] = gates.gates_pass_short(
            df,
            features[sym],
            earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            rrs_threshold=config.rrs_threshold_short,
            rrs_column=rrs_column,
            disabled=config.disabled_gates,
        )

    spy_stacked = stacked_count(spy)

    state = dict.fromkeys(bars_by_symbol, watchlist.IDLE)
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity = config.starting_equity
    equity_curve = []

    pending_entries: list[tuple[str, str]] = []  # (symbol, direction) to fill at next open

    for i, day in enumerate(calendar):
        # 1. fill pending entries/exits at today's open (decided at yesterday's close)
        for sym, direction in pending_entries:
            if sym in positions:
                continue
            is_long_full = direction == LONG and len(
                [p for p in positions.values() if p.direction == LONG]
            ) >= config.max_concurrent_long
            is_short_full = direction == SHORT and len(
                [p for p in positions.values() if p.direction == SHORT]
            ) >= config.max_concurrent_short
            if is_long_full or is_short_full:
                continue
            df = bars_by_symbol[sym].loc[calendar]
            entry_price = df["open"].iat[i]
            atr = features[sym]["atr_d1"].iat[i - 1] if i > 0 else float("nan")
            if pd.isna(atr) or atr <= 0:
                continue
            stop_dist = STOP_ATR_MULT * atr
            stop = entry_price - stop_dist if direction == LONG else entry_price + stop_dist
            risk_dollars = equity * config.risk_per_trade_pct
            size_mult = config.short_size_multiplier if direction == SHORT else 1.0
            shares = (risk_dollars / stop_dist) * size_mult
            positions[sym] = Position(
                symbol=sym,
                direction=direction,
                entry_date=day,
                entry_price=entry_price,
                shares=shares,
                stop=stop,
                entry_atr=atr,
            )
        pending_entries = []

        # 2. manage open positions using today's bar
        to_close: list[tuple[str, float, str]] = []
        for sym, pos in positions.items():
            df = bars_by_symbol[sym].loc[calendar]
            bar = df.iloc[i]
            pos.days_held += 1

            if pos.direction == LONG:
                if bar["low"] <= pos.stop:
                    to_close.append((sym, min(pos.stop, bar["open"]), "hard_stop"))
                    continue
            else:
                if bar["high"] >= pos.stop:
                    to_close.append((sym, max(pos.stop, bar["open"]), "hard_stop"))
                    continue

            bias = bias_df["bias"].iat[i]
            spy_stack = spy_stacked.iat[i]
            if pos.direction == LONG and bias in (BEAR, STRONG_BEAR) and spy_stack <= -2:
                to_close.append((sym, bar["close"], "market_flip"))
                continue
            if pos.direction == SHORT and bias in (BULL, STRONG_BULL) and spy_stack >= 2:
                to_close.append((sym, bar["close"], "market_flip"))
                continue

            rrs_now = features[sym]["rolling_rrs_d1"].iat[i]
            rrs_prev = features[sym]["rolling_rrs_d1"].iat[i - 1] if i > 0 else float("nan")
            if pos.direction == LONG and rrs_now < 0 and rrs_prev < 0:
                to_close.append((sym, bar["close"], "rrs_failure"))
                continue
            if pos.direction == SHORT and rrs_now > 0 and rrs_prev > 0:
                to_close.append((sym, bar["close"], "rrs_failure"))
                continue

            favorable = (
                (bar["close"] - pos.entry_price)
                if pos.direction == LONG
                else (pos.entry_price - bar["close"])
            )
            pos.peak_favorable = max(pos.peak_favorable, favorable)
            ha = features[sym]["ha_cont_d1"].iat[i]
            target = PROFIT_TARGET_ATR_MULT * pos.entry_atr
            if favorable >= target and (
                (pos.direction == LONG and ha <= 0) or (pos.direction == SHORT and ha >= 0)
            ):
                to_close.append((sym, bar["close"], "profit_take"))
                continue

            if pos.peak_favorable >= TRAIL_TRIGGER_ATR_MULT * pos.entry_atr:
                e8 = ema8[sym].iat[i]
                if pos.direction == LONG:
                    trail = e8 - TRAIL_STOP_ATR_MULT * pos.entry_atr
                    pos.stop = max(pos.stop, min(trail, pos.entry_price))
                else:
                    trail = e8 + TRAIL_STOP_ATR_MULT * pos.entry_atr
                    pos.stop = min(pos.stop, max(trail, pos.entry_price))

            if pos.days_held >= MAX_HOLD_DAYS:
                to_close.append((sym, bar["close"], "max_hold"))
                continue

        for sym, exit_price, reason in to_close:
            pos = positions.pop(sym)
            pnl_per_share = (
                (exit_price - pos.entry_price)
                if pos.direction == LONG
                else (pos.entry_price - exit_price)
            )
            pnl = pnl_per_share * pos.shares
            stop_dist = abs(pos.entry_price - pos.stop) or 1.0
            r_multiple = pnl_per_share / (STOP_ATR_MULT * pos.entry_atr)
            equity += pnl
            trades.append(
                Trade(
                    symbol=sym,
                    direction=pos.direction,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=day,
                    exit_price=exit_price,
                    shares=pos.shares,
                    exit_reason=reason,
                    pnl=pnl,
                    r_multiple=r_multiple,
                )
            )

        equity_curve.append(equity)

        # 3. update watchlist state + generate next-day entry signals
        bias_today = bias_df["bias"].iat[i]
        today_scores_long = {}
        for sym in bars_by_symbol:
            gp = bool(gate_long[sym].iat[i]) if not pd.isna(gate_long[sym].iat[i]) else False
            score = scores_long[sym].iat[i]
            rrs_now = features[sym]["rrs_d1"].iat[i]
            rrs_prev = features[sym]["rrs_d1"].iat[i - 1] if i > 0 else None
            state[sym] = watchlist.next_state_long(
                state[sym],
                gp,
                score,
                rrs_prev,
                rrs_now,
                min_list_score=config.min_list_score,
                min_hold_score=config.min_hold_score,
            )
            if gp and not pd.isna(score):
                today_scores_long[sym] = score

        trigger_today = bias_df["trigger"].iat[i]
        bias_ok_long = "bias" in config.disabled_gates or bias_today in (BULL, STRONG_BULL)
        if bias_ok_long:
            tradeable = watchlist.build_tradeable_list(
                today_scores_long,
                sectors,
                config.min_list_score,
                config.top_n_list,
                config.top_n_tradeable,
                config.max_per_sector,
            )
            # Path A (05 §2): on a trendline-breach trigger day, the top
            # tradeable RS symbols enter directly from QUALIFIED -- the
            # market pullback itself was the dip, no individual dip-arm
            # needed. Path B (05 §3, the watchlist state machine) covers
            # entries on non-trigger days once a symbol individually dips.
            eligible_states = (
                {watchlist.QUALIFIED, watchlist.ENTRY_EVAL}
                if trigger_today == LONG_TRIGGER
                else {watchlist.ENTRY_EVAL}
            )
            for sym in tradeable:
                if state[sym] in eligible_states and sym not in positions:
                    pending_entries.append((sym, LONG))

        if config.shorts_enabled:
            # Shorts skip the watchlist dip-arming state machine (gate+score
            # only) -- disabled by default (shorts_enabled=False), matching
            # 06's own recommended default; not worth a second full state
            # machine for a path that's off by default in this milestone.
            today_scores_short = {}
            for sym in bars_by_symbol:
                gp = bool(gate_short[sym].iat[i]) if not pd.isna(gate_short[sym].iat[i]) else False
                score = scores_short[sym].iat[i]
                if gp and not pd.isna(score):
                    today_scores_short[sym] = score
            if bias_today in (BEAR, STRONG_BEAR):
                tradeable_s = watchlist.build_tradeable_list(
                    today_scores_short,
                    sectors,
                    config.min_list_score,
                    config.top_n_list,
                    config.top_n_tradeable,
                    config.max_per_sector,
                )
                for sym in tradeable_s:
                    if sym not in positions:
                        pending_entries.append((sym, SHORT))

    equity_series = pd.Series(equity_curve, index=calendar)
    return BacktestResult(trades=trades, equity_curve=equity_series)
