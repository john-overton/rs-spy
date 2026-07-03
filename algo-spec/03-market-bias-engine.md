# 03 — Market Bias Engine (SPY/QQQ Read)

Purpose: produce the **market bias** that gates all trading ("Market First"). The engine reads
SPY (primary) and QQQ (tech confirmation) but never generates orders on them — the wiki finding
is that the index is the wind, not the trade.

Runs on every closed M5 bar of SPY, 09:30–16:00 ET, plus a daily-context pass before the open.

## 1. Outputs

```
bias        ∈ {STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR}
bias_score  ∈ [−100, +100]          # continuous, drives sizing multiplier
timing      ∈ {NONE, LONG_TRIGGER, SHORT_TRIGGER}   # trendline-breach event (see §5)
regime_d1   ∈ {TREND_UP, CHOP, TREND_DOWN}          # daily context
```

Gate mapping (wiki "Keeping it Really Simple" Rule 1 — hard, not advisory):

| bias | Longs | Shorts |
|------|-------|--------|
| STRONG_BULL / BULL | allowed | blocked |
| NEUTRAL | blocked | blocked |
| BEAR / STRONG_BEAR | blocked | allowed |

## 2. Daily-context pass (pre-open)

Computed from SPY D1 history; sets `regime_d1` and the day's prior expectations:

1. **Trend**: 20-day linear-regression slope of closes, sign-checked against SMA50 slope, and
   an upward/downward D1 trendline fit on pivot points (02 §9 on D1). Both agree up →
   `TREND_UP`; both down → `TREND_DOWN`; else `CHOP`.
2. **Range map**: prior day high/low/close, overnight gap, nearest major D1 support/resistance
   levels (pivot clusters + round SMAs). These become the intraday reference levels.
3. **Breakout audit** (real-vs-fake, from "How To Tell If This Breakout Is Real or Fake"): if a
   D1 breakout occurred in the last 3 sessions, check follow-through — closes above the
   breakout candle midpoint, rising volume on up moves, shallow dips. Follow-through present →
   breakout confirmed (adds to bull context). Tight ranges + light volume + bearish drift after
   the breakout → mark **suspect short-covering rally**; bias engine caps `bias` at BULL (never
   STRONG_BULL) and lowers the score by 15 while suspect.

`regime_d1 = CHOP` (the "overall-market-analysis" scenario) does not block trading — chop is
"an excellent environment for day trading" — but it disables the STRONG tiers and tightens
profit-taking (05/06).

## 3. Intraday bias score

On each M5 close, sum weighted components (weights chosen so hard signals dominate; recalibrate
in backtest):

| # | Component | Rule | Points |
|---|-----------|------|--------|
| 1 | VWAP side | SPY above session VWAP +20; below −20; within ±0.03% → 0 | ±20 |
| 2 | Candle structure | stacked green (02 §10) +20; stacked red −20; chop_ratio ≥ 0.6 → 0 and flags NEUTRAL pull | ±20 |
| 3 | Day range position | close in top third of day range +10; bottom third −10 | ±10 |
| 4 | Prior-day levels | above prior-day high +10; below prior-day low −10; inside 0 | ±10 |
| 5 | Trendline state | above intact intraday down-trendline after breach +10; mirrored −10 | ±10 |
| 6 | Volume confirmation | RVOL ≥ 1.2 in direction of move: amplify (±10 in move direction); RVOL < 0.8 on a rally after suspect breakout: −10 | ±10 |
| 7 | D1 regime agreement | intraday direction matches `regime_d1` trend +10; fights it −10; regime CHOP 0 | ±10 |
| 8 | QQQ agreement | QQQ same side of its VWAP as SPY +10; disagreement −10 | ±10 |

`bias_score` = clamped sum, then smoothed: `EMA(3 bars)` to prevent single-bar flip-flopping.

### Mapping score → bias

```
bias = STRONG_BULL  if score ≥ +60
       BULL         if +25 ≤ score < +60
       NEUTRAL      if −25 < score < +25          # "Market Undecided → No Trade"
       BEAR         if −60 < score ≤ −25
       STRONG_BEAR  if score ≤ −60
```

Hysteresis: leaving NEUTRAL requires the score to cross ±25 and *hold for 2 consecutive M5
bars*; falling back into NEUTRAL is immediate. This encodes "confirm rather than anticipate."

## 4. First-45-minutes observation window

From "A Simple Strategy": **no entries before 10:15 ET.** During 09:30–10:15 the engine only:

- classifies the open (gap-and-go, gap-fade, flat open probing support/resistance);
- watches whether opening drive holds VWAP and prior-day levels;
- feeds the selection engine, which is building the RS/RW watchlist;
- fits the initial intraday trendlines.

Bias output during this window is computed but marked `warmup=true`; the trade engine treats it
as NEUTRAL.

## 5. Timing trigger (trendline breach)

The entry *timing* signal from "A Simple Strategy" step 4–5:

```
LONG_TRIGGER  when: bias ∈ {BULL, STRONG_BULL}
              and SPY M5 close breaches the fitted intraday DOWN-trendline upward (02 §9)
              — or bias == STRONG_BULL with no down-trendline present (already trending;
                "If SPY is very bullish then you do not need to wait")

SHORT_TRIGGER mirrored: bias ∈ {BEAR, STRONG_BEAR} and up-trendline breached downward,
              or STRONG_BEAR with no up-trendline.
```

A trigger is an *event* consumed by the trade engine (05/06); it stays valid while bias remains
on the same side and the breach level holds on a closing basis.

## 6. Bias-flip handling

On any transition that removes permission (e.g. BULL → NEUTRAL/BEAR):

1. New entries in the now-blocked direction stop immediately.
2. Open positions are not auto-flattened on NEUTRAL — they are managed by their own exit rules,
   with tightened stops (07 §4). On a full flip to the opposite side (BULL → BEAR) with stacked
   candles + RVOL ≥ 1.5 ("market stacking red candles — get the hell out"), all opposing
   positions are closed at market.

## 7. Scheduled-event blackout

No new entries within ±15 minutes of scheduled macro releases (FOMC statement, CPI, PPI, NFP,
FOMC minutes). Configurable calendar; bias keeps computing throughout.
