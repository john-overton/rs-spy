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
from rs_spy.algo import short as short_algo
from rs_spy.bias.daily_context import daily_context_series
from rs_spy.bias.engine import bias_series
from rs_spy.data.resample import align_daily_to_intraday
from rs_spy.indicators.atr import atr as atr_fn
from rs_spy.selection import gates, scoring
from rs_spy.selection.features_m5 import RRS_M5_WINDOW, compute_symbol_features_m5

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
    rrs_m5_window: int = RRS_M5_WINDOW
    use_qqq_crosscheck: bool = False
    max_entries_per_symbol_long: int = 2
    max_entries_per_symbol_short: int = 1
    expected_hold_minutes: float = 120.0
    unfilled_cancel_bars: int = 2


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
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
        ).fillna(False)
        gs_native = gates.gates_pass_short_m5(
            df_m5_native, feat_native, earnings_blackout.get(sym),
            min_adv_shares=config.min_adv_shares,
            use_qqq_crosscheck=config.use_qqq_crosscheck,
            disabled=config.disabled_gates,
        ).fillna(False)
        sl_native = scoring.score_long_m5(feat_native)
        ss_native = scoring.score_short_m5(feat_native)

        rs_fail_l_native = long_algo.rs_failure_long(feat_native["rolling_rrs_m5"])
        rs_fail_s_native = short_algo.rs_failure_short(feat_native["rolling_rrs_m5"])
        vwap_l_native = long_algo.vwap_loss_long(feat_native["close"], feat_native["vwap_m5"])
        vwap_s_native = short_algo.vwap_loss_short(feat_native["close"], feat_native["vwap_m5"])
        stall_l_native = long_algo.momentum_stall_long(feat_native["lrsi_m5"])
        stall_s_native = short_algo.momentum_stall_short(feat_native["lrsi_m5"])
        confirm_l_native = long_algo.confirm_trigger_entry_long(feat_native, ema8_native, atr_native)
        confirm_s_native = short_algo.confirm_trigger_entry_short(feat_native, ema8_native, atr_native)
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
    )
