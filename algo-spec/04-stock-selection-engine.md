# 04 — Stock Selection Engine (RS/RW Scanner)

Purpose: continuously rank the trade universe into an **RS list** (long candidates) and an
**RW list** (short candidates). "A list that should constantly be changing and updating
throughout the day."

Runs on every closed M5 bar for all universe symbols (vectorized scan), starting 09:35.

## 1. The four target profiles

From "Simple and Effective Day Trading Method" — a candidate must fit one of:

| Profile | Market | Stock | List |
|---------|--------|-------|------|
| P1 | SPY up | stock up proportionally more (RRS ≫ 0) | RS |
| P2 | SPY up | stock down (extreme RW) | RW |
| P3 | SPY down | stock down proportionally more (RRS ≪ 0) | RW |
| P4 | SPY down | stock up (extreme RS) | RS |

P2/P4 (fighting the tape) are the strongest signals and receive the divergence bonus in §4.

## 2. Hard gates (all must pass before scoring)

A symbol failing any gate is excluded regardless of score. Long gates shown; shorts mirror.

| # | Gate | Rule | Source |
|---|------|------|--------|
| G1 | Universe | passes 01 §4 (price ≥ $10, ADV ≥ 1 M, float ≥ 50 M, …) | wiki |
| G2 | M5 RS | `RollingRRS_M5 ≥ +1.0` | 02 §1.4 |
| G3 | VWAP | last close above session VWAP | Rule 2 |
| G4 | D1 HA continuation | `ha_cont_d1 ≥ +2` | Rule 3 |
| G5 | D1 SMA stack | `ABOVE_ALL` (50/100/200) | Rule 4 |
| G6 | Headroom | `headroom ≥ 1.0 × ATR_D1` to nearest resistance | "A Simple Strategy" |
| G7 | Volume | `RVOL ≥ 1.0` | "good volume" |
| G8 | Earnings | no earnings today / tomorrow pre-market | risk hygiene |
| G9 | Benchmark cross-check | if QQQ-denominated RRS enabled for the symbol's group, RS must hold vs both benchmarks | 01 §1 |

## 3. Anti-pattern exclusions

- **One-candle wonder**: `RollingRRS_M5 ≥ 1.0` but a single M5 bar contributes > 60% of the
  window's price change → exclude until the rolling average confirms (this is what Rolling RRS
  is designed to penalize; the explicit check catches fresh spikes inside the window).
- **News-halt churn**: halted intraday → excluded for the rest of the session.
- **Low-priced gapper behavior**: gap > 20% at open → excluded for the day (momentum-gapper
  regime, out of scope).
- **Post-breakout suspect**: D1 breakout within last 3 sessions that failed follow-through
  (03 §2.3) → long score penalized (see W6).

## 4. Composite score (0–100)

Weights encode the wiki's stated hierarchy: D1 context outranks M5 snapshot; RS/RW magnitude
and market alignment dominate; volume validates.

| # | Component | Weight | Scoring (long side) |
|---|-----------|--------|---------------------|
| W1 | M5 Rolling RRS magnitude | 25 | linear 0→25 as RollingRRS goes 1.0→3.0, cap 25 |
| W2 | D1 RRS (5-day) | 20 | linear 0→20 as RRS_D1 goes 0.5→2.0 |
| W3 | D1 chart quality | 15 | HA continuation length (2d=6, 3d=9, ≥4d=12) + 3 if 8-EMA(D1) preserved on all pullbacks in window |
| W4 | Divergence bonus (P4/P2) | 15 | stock green while `PowerIndex ≤ −1.0` (or holding flat, RRS ≥ 2) = 15; normal P1/P3 proportional strength = up to 8 |
| W5 | Volume | 10 | RVOL 1.0→2.0 maps 0→10 |
| W6 | Headroom | 10 | headroom 1.0→2.0+ ATR maps 0→10; −5 penalty if post-breakout suspect |
| W7 | Consistency | 5 | std-dev of per-bar RRS over window in lowest tercile of candidates = 5 (smooth accumulation beats jumpy) |

`score_long = Σ`, computed only for gate-passing symbols. `score_short` is the mirror using
RW values (RollingRRS ≤ −1.0, BELOW_ALL, below VWAP, support headroom, HA red continuation).

## 5. List construction

Every M5 close:

1. Score all gate-passing symbols both directions.
2. **RS list** = top 20 by `score_long`, minimum score 50. **RW list** = top 20 by
   `score_short`, min 50.
3. Tradeable subset = top 5 of the list matching current market bias ("you should have at
   least 3–5 good stocks").
4. Sector concentration: max 2 tradeable candidates per GICS sector (avoid a disguised single
   sector bet).
5. Stickiness: a symbol already on the list keeps its slot unless its score drops below 40 or
   a gate fails — prevents churn at the boundary. A symbol whose **gate fails** (e.g. loses
   VWAP or RollingRRS < 0) is removed immediately and flagged to the trade engine if a
   position is open.

## 6. Watchlist state machine (per symbol)

From "Don't Overthink This" — spot strength, then **wait for the dip**; never chase:

```
          gates pass, score ≥ 50            RRS(t) crosses < 0 then > 0
 IDLE ───────────────────────▶ QUALIFIED ─────────────────────────────▶ DIP_ARMED
                                   │        (or LRSI < 20 then > 20)        │
                                   │                                        │ trade engine
                                   │ score < 40 or gate fail                │ evaluates entry
                                   ▼                                        ▼
                                 IDLE                                   ENTRY_EVAL
```

`QUALIFIED` symbols are not entered on discovery ("the stock is typically strong when I spot
it"). They arm dip alerts. `DIP_ARMED → ENTRY_EVAL` requires the market bias gate and timing
conditions in 05/06 to also hold, otherwise the alert resets and re-arms.

Exception — **trigger-day entry** (from "A Simple Strategy"): when the bias engine fires
`LONG_TRIGGER` (SPY down-trendline breach), the top tradeable RS symbols may be entered
directly from `QUALIFIED` without an individual dip, because the market pullback itself was
the dip. Same for `SHORT_TRIGGER` and RW symbols.
