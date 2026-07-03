# 06 — Short-Bias Algorithm

Mirror of the long algorithm, trading RW stocks short when the market is bearish. The wiki
flags shorting as harder ("shorting is only for seasoned Pros") — the short module therefore
runs with **stricter gates and smaller size**, and can be disabled by config
(`shorts_enabled=false` is the recommended default until the long side validates in paper
trading).

## 1. Preconditions (hard; differences from 05 in bold)

1. Time ≥ 10:15 ET, ≤ 15:30 ET.
2. `bias ∈ {BEAR, STRONG_BEAR}` for ≥ 2 consecutive M5 bars, **and `regime_d1 ≠ TREND_UP`** —
   never short a confirmed daily uptrend ("trade as if every dip is a buying opportunity"
   until the D1 up-trendline breaks on a long red candle with very heavy volume).
3. No scheduled-event blackout.
4. Risk slots available; **short size multiplier 0.75×** (07 §2).
5. Symbol on the tradeable RW subset: `RollingRRS_M5 ≤ −1.0`, below VWAP, `ha_cont_d1 ≤ −2`,
   `BELOW_ALL` SMA stack, downside headroom ≥ 1.0 × ATR_D1 to nearest support, RVOL ≥ 1.0,
   shares available to borrow / not hard-to-borrow, **not** > 15% short interest with gap-down
   (squeeze fuel guard).

## 2. Entry path A — market trigger day

```
on SHORT_TRIGGER (SPY breaks intraday UP-trendline downward, bias BEAR/STRONG_BEAR):
    for symbol in tradeable_RW[:max_new]:
        confirm on trigger bar close:
            RollingRRS_M5 ≤ −1.0 still true
            symbol below VWAP
            not extended: EMA8(M5) − close ≤ 1.0 × ATR_M5     # don't chase the flush
        → submit short entry
```

## 3. Entry path B — bounce re-entry

Mirror of the dip buy: short the weak bounce in a confirmed-weak stock.

```
DIP_ARMED (short) when RRS crosses > 0 then < 0, or LRSI > 80 then < 80 on M5
on event:
    require bias still BEAR/STRONG_BEAR
    bounce quality:  PASS if bounce was wimpy — mixed overlapping candles, light volume
                     (RVOL of bounce bars < 1.0), stayed below VWAP
                     FAIL if stacked green candles on volume → real buying; reset on M15
    require D1 gates still green
    → submit short entry
```

This is exactly the "shorts want to see a wimpy, light-volume bounce with mixed overlapping
candles" read from "How To Tell If This Breakout Is Real or Fake."

## 4. Position management (mirrored, with squeeze guards)

Same evaluation order as 05 §4 with directions flipped, plus:

- **Squeeze guard**: any M5 bar against the position ≥ 2.0 × ATR_M5 on RVOL ≥ 2.0 → exit
  immediately regardless of RRS (violent spikes: "when they happen you are managing losses on
  shorts instead of focusing on new longs").
- **Market flip to bull**: bias → BULL/STRONG_BULL → exit all shorts at market (not merely
  tightened — asymmetric vs the long side because upside squeezes are faster).
- Profit-take on LRSI crossing up through 20 with gain ≥ 1.0 × ATR_M5; trail via
  EMA8(M5) + 0.25 × ATR_M5 after 1.5 × ATR_M5 gain; flat by 15:55 ET.

## 5. Sizing and limits

All 07 limits apply with: size multiplier 0.75×, max concurrent shorts 3 (vs 5 longs), max 1
entry per symbol per day, session lockout after any stop-out.
