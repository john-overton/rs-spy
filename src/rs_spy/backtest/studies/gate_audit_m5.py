"""Full-universe M5 gate-pass-rate / watchlist-state audit. Not one of
algo-spec/08 Sec3's five named studies -- a diagnostic tool extending
`.superpowers/sdd/task9_diagnostic.py` (a 7-symbol scratch script written
during M6 to root-cause the first real M6 backtest's 0-trade result) into a
committed, reusable tool that runs the same audit across the FULL curated
universe, to confirm a small-sample finding actually generalizes -- the
same "expand and reconfirm" step M3.5 took at D1 cadence before trusting its
own diagnosis.

Scope note: this audit computes each symbol's gates/scores/watchlist state
independently on that symbol's OWN NATIVE M5 index (native-first, matching
this project's core precompute invariant -- see engine_m5.py's own
docstring), and simulates next_state_long/next_state_short per symbol in
isolation. It does NOT reproduce the real backtest's cross-symbol
top_n_list/top_n_tradeable ranking or max_per_sector caps (backtest/
engine_m5.py's run_m5_backtest does that) -- those can only ever make real
trade opportunities MORE scarce than what this audit measures, never less,
so on THAT axis this audit's DIP_ARMED/ENTRY_EVAL counts are an upper bound.

But this audit does NOT call `watchlist.apply_trigger_bypass` -- 04 Sec6's
exception that sends an already-QUALIFIED symbol straight to ENTRY_EVAL on
a matching LONG_TRIGGER/SHORT_TRIGGER bar from the bias engine, bypassing
DIP_ARMED entirely. `run_m5_backtest`'s real event loop does call it (see
engine_m5.py). Confirmed directly against the real post-fix backtest run
(IMPLEMENTATION.md's M7 section): a run that produced 0/128 symbols ever
reaching long DIP_ARMED in THIS audit still produced 3 real LONG trades,
all three entering exactly one bar after a LONG_TRIGGER fire -- i.e. 100%
of realized trades used the bypass path this audit doesn't model. So on
THIS axis, this audit's DIP_ARMED/ENTRY_EVAL counts are an UNDERcount of
real reachability, not an upper bound -- the two omissions pull in opposite
directions and neither should be read as a net bound on the real system.
Only the per-gate and joint pass-rate numbers (which don't involve the
watchlist state machine at all) are unaffected by either omission.
"""
import pandas as pd

from rs_spy.backtest.engine_m5 import ADV_LOOKBACK_DAYS, BacktestConfigM5
from rs_spy.data.resample import align_daily_to_intraday
from rs_spy.selection import gates as gates_mod
from rs_spy.selection import scoring, watchlist
from rs_spy.selection.features_m5 import compute_symbol_features_m5


def _long_gate_checks(df: pd.DataFrame, feat: pd.DataFrame, adv20: pd.Series, config: BacktestConfigM5) -> dict:
    return {
        "price": gates_mod.gate_price(df),
        "adv": gates_mod.gate_adv(df, min_shares=config.min_adv_shares, adv=adv20),
        "rrs_d1": gates_mod.gate_rrs_long(feat),
        "ha": gates_mod.gate_ha_long(feat),
        "sma": gates_mod.gate_sma_long(feat),
        "headroom": gates_mod.gate_headroom_long(feat),
        "volume_d1": gates_mod.gate_volume(feat),
        "rrs_m5": gates_mod.gate_rrs_m5_long(feat),
        "vwap": gates_mod.gate_vwap_long(feat),
        "not_one_candle_wonder": gates_mod.gate_not_one_candle_wonder(feat),
        "no_gap_exclusion": gates_mod.gate_no_gap_exclusion(feat),
    }


def _short_gate_checks(df: pd.DataFrame, feat: pd.DataFrame, adv20: pd.Series, config: BacktestConfigM5) -> dict:
    return {
        "price": gates_mod.gate_price(df),
        "adv": gates_mod.gate_adv(df, min_shares=config.min_adv_shares, adv=adv20),
        "rrs_d1": gates_mod.gate_rrs_short(feat),
        "ha": gates_mod.gate_ha_short(feat),
        "sma": gates_mod.gate_sma_short(feat),
        "headroom": gates_mod.gate_headroom_short(feat),
        "volume_d1": gates_mod.gate_volume(feat),
        "rrs_m5": gates_mod.gate_rrs_m5_short(feat),
        "vwap": gates_mod.gate_vwap_short(feat),
        "not_one_candle_wonder": gates_mod.gate_not_one_candle_wonder(feat),
        "no_gap_exclusion": gates_mod.gate_no_gap_exclusion(feat),
    }


def symbol_gate_rates(
    sym: str, df_m5: pd.DataFrame, feat: pd.DataFrame, adv20: pd.Series,
    config: BacktestConfigM5, earnings_blackout: set | None = None,
) -> tuple[dict, pd.Series, pd.Series]:
    """Per-gate and joint pass rates (long and short) for one symbol's
    native M5 data. `df_m5`/`feat`/`adv20` must already share the same
    native index. Returns (summary_row, gate_long_series, gate_short_series)
    -- the two series are returned so callers (e.g. the watchlist-reach
    step) don't have to recompute the joint gate twice."""
    long_checks = _long_gate_checks(df_m5, feat, adv20, config)
    short_checks = _short_gate_checks(df_m5, feat, adv20, config)

    row = {"symbol": sym, "n_native_bars": len(df_m5)}
    for name, series in long_checks.items():
        row[f"long_{name}_pct"] = 100.0 * series.fillna(False).mean()
    for name, series in short_checks.items():
        row[f"short_{name}_pct"] = 100.0 * series.fillna(False).mean()

    gl = gates_mod.gates_pass_long_m5(
        df_m5, feat, earnings_blackout, min_adv_shares=config.min_adv_shares,
        use_qqq_crosscheck=config.use_qqq_crosscheck, disabled=config.disabled_gates, adv20=adv20,
    ).fillna(False)
    gs = gates_mod.gates_pass_short_m5(
        df_m5, feat, earnings_blackout, min_adv_shares=config.min_adv_shares,
        use_qqq_crosscheck=config.use_qqq_crosscheck, disabled=config.disabled_gates, adv20=adv20,
    ).fillna(False)
    row["long_joint_pct"] = 100.0 * gl.mean()
    row["long_joint_bars"] = int(gl.sum())
    row["short_joint_pct"] = 100.0 * gs.mean()
    row["short_joint_bars"] = int(gs.sum())
    return row, gl, gs


def symbol_watchlist_reach(
    feat: pd.DataFrame, gate_long: pd.Series, gate_short: pd.Series, config: BacktestConfigM5,
) -> dict:
    """Simulates next_state_long/next_state_short independently against this
    symbol's own native gate/score series (see module docstring's scope
    note -- no cross-symbol ranking is modeled). Returns bar-counts-by-state
    and the set of states ever reached, for both directions."""
    n_bars = len(feat)
    score_long = scoring.score_long_m5(feat)
    score_short = scoring.score_short_m5(feat)
    rrs = feat["rolling_rrs_m5"]
    lrsi = feat["lrsi_m5"]

    result = {}
    for direction, gate, score in (("long", gate_long, score_long), ("short", gate_short, score_short)):
        state = watchlist.IDLE
        counts = {watchlist.IDLE: 0, watchlist.QUALIFIED: 0, watchlist.DIP_ARMED: 0, watchlist.ENTRY_EVAL: 0}
        ever_reached = {watchlist.IDLE}
        next_state_fn = watchlist.next_state_long if direction == "long" else watchlist.next_state_short
        for i in range(n_bars):
            rrs_prev = rrs.iat[i - 1] if i > 0 else None
            lrsi_prev = lrsi.iat[i - 1] if i > 0 else None
            state = next_state_fn(
                state, bool(gate.iat[i]), score.iat[i], rrs_prev, rrs.iat[i],
                lrsi_prev=lrsi_prev, lrsi_now=lrsi.iat[i],
                min_list_score=config.min_list_score, min_hold_score=config.min_hold_score,
            )
            counts[state] = counts.get(state, 0) + 1
            ever_reached.add(state)
        result[f"{direction}_bars_idle"] = counts[watchlist.IDLE]
        result[f"{direction}_bars_qualified"] = counts[watchlist.QUALIFIED]
        result[f"{direction}_bars_dip_armed"] = counts[watchlist.DIP_ARMED]
        result[f"{direction}_bars_entry_eval"] = counts[watchlist.ENTRY_EVAL]
        result[f"{direction}_ever_reached"] = ",".join(sorted(ever_reached))
    return result


def run_gate_pass_audit(
    universe_m1: dict, universe_m5: dict, universe_d1: dict,
    spy_m1: pd.DataFrame, spy_m5: pd.DataFrame, spy_d1: pd.DataFrame,
    qqq_m5: pd.DataFrame | None = None,
    earnings_blackout: dict | None = None,
    config: BacktestConfigM5 | None = None,
) -> dict:
    """Runs the gate-pass-rate + watchlist-state audit across every symbol
    in universe_m5. Returns {"per_gate": DataFrame, "watchlist": DataFrame,
    "summary": dict}."""
    config = config or BacktestConfigM5()
    earnings_blackout = earnings_blackout or {}

    per_gate_rows = []
    watchlist_rows = []
    for sym, df_m5 in universe_m5.items():
        feat = compute_symbol_features_m5(
            universe_m1[sym], df_m5, universe_d1[sym], spy_m1, spy_m5, spy_d1,
            qqq_m5=qqq_m5 if config.use_qqq_crosscheck else None,
            rrs_window=config.rrs_m5_window,
        )
        adv20_daily = universe_d1[sym]["volume"].rolling(ADV_LOOKBACK_DAYS).mean()
        adv20 = align_daily_to_intraday(adv20_daily, df_m5.index)

        row, gl, gs = symbol_gate_rates(sym, df_m5, feat, adv20, config, earnings_blackout.get(sym))
        per_gate_rows.append(row)

        wl_row = {"symbol": sym}
        wl_row.update(symbol_watchlist_reach(feat, gl, gs, config))
        watchlist_rows.append(wl_row)

    per_gate = pd.DataFrame(per_gate_rows)
    watchlist_df = pd.DataFrame(watchlist_rows)

    summary = {
        "n_symbols": len(universe_m5),
        "long_joint_pct_min": per_gate["long_joint_pct"].min(),
        "long_joint_pct_mean": per_gate["long_joint_pct"].mean(),
        "long_joint_pct_median": per_gate["long_joint_pct"].median(),
        "long_joint_pct_max": per_gate["long_joint_pct"].max(),
        "short_joint_pct_min": per_gate["short_joint_pct"].min(),
        "short_joint_pct_mean": per_gate["short_joint_pct"].mean(),
        "short_joint_pct_median": per_gate["short_joint_pct"].median(),
        "short_joint_pct_max": per_gate["short_joint_pct"].max(),
        "n_symbols_ever_long_dip_armed": int(watchlist_df["long_ever_reached"].str.contains("DIP_ARMED").sum()),
        "n_symbols_ever_long_entry_eval": int(watchlist_df["long_ever_reached"].str.contains("ENTRY_EVAL").sum()),
        "n_symbols_ever_short_dip_armed": int(watchlist_df["short_ever_reached"].str.contains("DIP_ARMED").sum()),
        "n_symbols_ever_short_entry_eval": int(watchlist_df["short_ever_reached"].str.contains("ENTRY_EVAL").sum()),
    }
    return {"per_gate": per_gate, "watchlist": watchlist_df, "summary": summary}
