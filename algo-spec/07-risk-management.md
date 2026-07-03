# 07 — Risk Management

The wiki's "Great Imbalance" diagnosis: most picks are eventually right; traders lose because
of theta decay, panic exits, and oversized positions locking up buying power. The risk layer is
designed around those three failure modes — small consistent risk, technical (not P&L-panic)
stops, and never concentrating the account.

## 1. Account-level parameters (defaults; config)

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Risk per trade (R) | 0.5% of equity | survive long losing streaks while validating |
| Max concurrent positions | 5 long / 3 short | matches "3–5 good stocks" watchlist depth |
| Max gross exposure | 100% of equity (no margin in v1) | buying-power failure mode |
| Max per-sector exposure | 40% of gross | hidden correlation guard |
| Daily loss limit | −2.0% of equity | hard kill: flatten all, no entries until next session |
| Weekly loss limit | −4.0% of equity | disable system, require human review |
| Consecutive stop-outs | 3 in a day | halt new entries for 2 hours (market read is wrong) |

## 2. Position sizing

Fixed-fractional risk against the technical stop, scaled by conviction:

```
stop_distance = entry − stop_price                  # see §3
base_shares   = (equity × R) / stop_distance
size_mult     = bias_mult × score_mult × side_mult
    bias_mult :  STRONG tier 1.0, normal tier 0.75
    score_mult:  candidate score 50→100 maps 0.7→1.0
    side_mult :  long 1.0, short 0.75
shares = floor(base_shares × size_mult)
cap: position notional ≤ 20% of equity; shares ≤ 5% of symbol's 20-day ADV / 390 × expected
     hold minutes (participation sanity cap)
```

## 3. Stops (technical, placed as resting orders at entry)

Long stop = lowest of the qualifying dip's swing low and (entry − 1.0 × ATR_M5(14)), but never
wider than 1.5 × ATR_M5 — if structure demands a wider stop, the entry is skipped rather than
the stop widened. Shorts mirrored (swing high of the bounce).

Stops are never moved away from price. They move only toward it (trail rules in 05/06 §4).

## 4. Dynamic tightening

- Bias falls to NEUTRAL while holding: tighten stop to entry − 0.5 × ATR_M5 (or breakeven if
  better) and disable adds/partial re-entries.
- Final 30 minutes (15:30–16:00): profit-take thresholds reduced 25%, all positions flat by
  15:55 ET via market orders.
- `regime_d1 == CHOP`: targets reduced (05 §4.5); trail engaged one step earlier.

## 5. Order handling

- Entries: marketable limit at last + 0.1 × ATR_M5 (long) to bound slippage; unfilled after
  2 M5 bars → cancel (never chase; the state machine will re-arm).
- Exits on signal: market orders (getting out matters more than the fill).
- All orders day-TIF, regular session only.

## 6. Kill switches / operational guards

| Trigger | Action |
|---------|--------|
| Data feed gap > 60 s on SPY or any held symbol | no new entries; if > 3 min, flatten all |
| Broker reject/error on exit order | retry ×3 then alert human, attempt market flatten |
| Position exists that the state machine can't map | alert + flatten |
| Clock skew > 2 s vs exchange time | halt entries |
| Symbol halted while held | mark unmanaged, alert human (LULD reopen handling is manual in v1) |

## 7. What this layer deliberately does not do

- No martingale, no averaging down, no "widening the stop because the thesis is intact."
- No overnight holds (removes the wiki's earnings/news gap risk from the day-trade book).
- No discretionary override hooks in live mode — parameter changes take effect next session,
  never mid-position.
