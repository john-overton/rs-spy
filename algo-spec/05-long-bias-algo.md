# 05 — Long-Bias Algorithm

Trades RS stocks long when the market permits. Consumes: `bias`/`timing` from the bias engine
(03), the RS tradeable list and per-symbol state machine from selection (04), and limits from
risk (07).

## 1. Preconditions (all hard)

1. Time ≥ **10:15 ET** (post observation window) and ≤ **15:30 ET** (no new entries after).
2. `bias ∈ {BULL, STRONG_BULL}` for ≥ 2 consecutive M5 bars.
3. No scheduled-event blackout active.
4. Risk manager has available slots and buying power (07).
5. Symbol is on the **tradeable RS subset** (top-5, score ≥ 50, all gates green).

## 2. Entry path A — market trigger day ("A Simple Strategy")

The canonical sequence: observe 45 min → build RS list → wait for SPY to break its intraday
down-trendline → buy the strong stocks.

```
on LONG_TRIGGER from bias engine:
    for symbol in tradeable_RS[:max_new]:            # best scores first
        confirm on trigger bar close:
            RollingRRS_M5 ≥ 1.0 still true           # "as long as they have maintained RS"
            symbol above VWAP
            symbol M5 bar not extended: close − EMA8(M5) ≤ 1.0 × ATR_M5(14)  # don't chase
        → submit entry (marketable limit, ≤ last + 0.1 × ATR_M5)
```

If SPY had no down-trendline (already trending up, STRONG_BULL), path A degenerates to path B.

## 3. Entry path B — stock dip re-entry ("Don't Overthink This")

For symbols in `DIP_ARMED` (RRS crossed < 0 then > 0, or LRSI < 20 then > 20):

```
on dip-reset event for symbol:
    require bias still BULL/STRONG_BULL
    evaluate the dip quality on M5 since QUALIFIED:
        PASS if pullback was: mixed overlapping candles, RVOL(pullback bars) < 1.0,
                depth ≤ 1.5 × ATR_M5 below the local high, VWAP held
        FAIL if: stacked red candles or heavy-volume drop  → expect more downside;
                reset alert on M15 timeframe instead
    require D1 picture unchanged (gates G4–G6 still green)
    → submit entry
```

Path B is the primary flow for the rest of the day after the morning trigger; it buys pullbacks
in confirmed-strong stocks with the market tailwind, never breakouts.

## 4. Position management

On each M5 close per open position, evaluate in order:

1. **Hard stop** (07 §3): technical stop hit intra-bar → already exited by resting order.
2. **Market flip**: bias → BEAR/STRONG_BEAR with stacked red + RVOL ≥ 1.5 → exit at market
   ("you were on the wrong side of the market — get out").
3. **RS failure**: `RollingRRS_M5 < 0` for 2 consecutive bars → exit. One bar < 0 with the
   market drifting (not stacking red), stock holding EMA8(M5) and up-trendline → hold
   ("weather the storm" case from "If We All Trade RS/RW…").
4. **VWAP loss**: two consecutive M5 closes below VWAP → exit.
5. **Momentum-stall profit take**: LRSI crosses down through 80 **and** unrealized gain
   ≥ 1.0 × ATR_M5 above entry → take profit ("take gains when the bounce stalls"). In
   `regime_d1 == CHOP`, take at 0.75 × the normal target (chop pays faster exits).
6. **Trail**: after gain ≥ 1.5 × ATR_M5, trail stop to max(EMA8(M5) − 0.25 × ATR_M5, entry).
7. **Time flat**: all positions closed by **15:55 ET** (day-trade only, v1).

Scaling: optional 50% partial at +1.0 × ATR_M5, remainder on trail — configurable, default on.

## 5. Re-entry

After a profit-take or RS-failure exit, the symbol returns to `QUALIFIED` and must re-arm a
fresh dip cycle before re-entry. Max 2 entries per symbol per day. After a hard-stop loss on a
symbol, it is locked out for the session.
