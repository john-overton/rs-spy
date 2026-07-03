# 02 — Indicators and Calculations

All formulas the system computes. Bars are regular-session unless noted. `t` indexes the
current closed bar; calculations run **on bar close only** (no intra-bar repainting).

## 1. Real Relative Strength (RRS) — the core edge

Source: "A New Measure of Relative Strength" (H.S.), matching the community-standard
implementation (`RRS = (PC − expectedPC) / ATR`).

Plain percent-change comparison is inadequate: it ignores each instrument's normal volatility.
RRS asks: *given how far SPY moved relative to its own average range, how far should this stock
have moved — and how far did it actually move, in units of its own average range?*

### 1.1 Inputs

- `L` — measurement window in bars. **M5: L = 12** (1 hour). **D1: L = 5** (1 week).
- `ATR_S` — stock's average true range over the same span as `L`. For M5 use
  **ATR50 on H1 bars** (average hourly range over the last 50 hours), per the wiki. Practical
  equivalent when only M5 bars are available: `ATR(12·50 = 600 M5 bars)` summed to hourly, or
  `WildersATR(H1, 50)`. For D1 use `WildersATR(D1, 14)`.
- `ATR_M` — same measure computed on SPY.

### 1.2 Per-window calculation

```
PC_S(t)      = Close_S(t) − Close_S(t−L)          # stock price change over window, $
PC_M(t)      = Close_M(t) − Close_M(t−L)          # SPY price change over window, $

PowerIndex(t) = PC_M(t) / ATR_M                    # "SPY Power Index": how many multiples of
                                                   # its normal range SPY moved

ExpectedPC_S(t) = PowerIndex(t) × ATR_S            # what the stock "should" have moved

RRS(t) = (PC_S(t) − ExpectedPC_S(t)) / ATR_S       # excess move in units of the stock's ATR
```

Interpretation: `RRS = +3.0` → the stock moved 3 stock-ATRs *more* than SPY's move predicted.
`RRS < 0` → relative weakness. Zero-centered oscillator; comparable across symbols because it
is normalized by each stock's own ATR (this also fixes the $30-stock vs $300-stock problem
called out in "How to Monitor Relative Strength vs SPY").

### 1.3 Rolling RRS (anti-one-candle-spike)

A single large candle (one block order) produces a false RS reading that then decays. To
penalize bursts and reward *consistent* institutional accumulation, the tradable signal is the
mean of per-bar RRS values:

```
RollingRRS(t) = mean( RRS(t−i) for i in 0..L−1 )    # L = 12 on M5
```

`RollingRRS` is the primary ranking/gating value (`rrs_m5` in the data model). Raw `RRS(t)`
is kept for dip-detection crossings (see 05 §3).

### 1.4 Thresholds (initial calibration, subject to backtest)

| Signal | Value |
|--------|-------|
| RS qualification (long candidates) | `RollingRRS_M5 ≥ +1.0` |
| RW qualification (short candidates) | `RollingRRS_M5 ≤ −1.0` |
| "Pure strength" flag (holds bid during market drop) | `RollingRRS_M5 ≥ +2.0` while `PowerIndex ≤ −1.5` |
| RS faded (exit input) | `RollingRRS_M5 < 0` |
| D1 alignment | `RRS_D1 > +0.5` for longs, `< −0.5` for shorts |

D1 RRS outranks M5 RRS in importance ("relative strength on a D1 basis is more relevant than
relative strength on an M5 basis") — encoded in the selection weights (04 §4).

## 2. ATR

Wilder's smoothing:

```
TR(t)  = max(High−Low, |High−PrevClose|, |Low−PrevClose|)
ATR(t) = (ATR(t−1)×(n−1) + TR(t)) / n
```

Instances: `ATR(H1, 50)` for RRS-M5; `ATR(D1, 14)` for RRS-D1, headroom, stops, and sizing.

## 3. VWAP

Session VWAP from 1-min bars, 09:30 anchor:

```
VWAP(t) = Σ(TypicalPrice_i × Volume_i) / Σ(Volume_i),   TypicalPrice = (H+L+C)/3
```

Hard rule (wiki Rule 2): no short entries above M5 VWAP, no long entries below M5 VWAP.

## 4. Relative Volume (RVOL)

Time-of-day adjusted so 10:00 volume isn't compared to a full-day average:

```
CumVol(t)      = session volume through bar t
ExpCumVol(t)   = mean over prior 20 sessions of cumulative volume through same time-of-day
RVOL(t)        = CumVol(t) / ExpCumVol(t)
```

Qualification: `RVOL ≥ 1.5` scores full volume points; `≥ 1.0` partial; `< 1.0` fails the
volume gate for new entries. Heavy volume on breakouts (`RVOL ≥ 2.0`) feeds the
breakout-confirmation logic (03 §4).

## 5. Heikin-Ashi continuation (D1)

```
HA_Close = (O+H+L+C)/4
HA_Open  = (prev HA_Open + prev HA_Close)/2
```

- Bullish HA continuation day: `HA_Close > HA_Open` **and** flat/no bottom wick
  (`HA_Open == HA_Low` within tolerance `0.05 × ATR_D1`).
- `ha_cont_d1` = count of consecutive qualifying days, signed (+ bullish / − bearish).

Hard rule (wiki Rule 3): entries require `|ha_cont_d1| ≥ 2` in the trade direction.

## 6. SMA stack (D1)

Major SMAs: **50, 100, 200** (daily closes).

```
sma_stack = ABOVE_ALL  if Close > SMA50 and Close > SMA100 and Close > SMA200
          = BELOW_ALL  if Close < all three
          = MIXED      otherwise
```

Hard rule (wiki Rule 4): longs require `ABOVE_ALL`, shorts require `BELOW_ALL`.

## 7. Headroom to resistance / support (D1)

"You don't want a stock that has the 200 SMA sitting 15 cents above the current price."

Resistance set for longs (support for shorts, mirrored):
- any major SMA above price,
- most recent swing highs (pivot highs with 5-bar left/right strength, last 60 sessions),
- round 52-week high.

```
headroom = (nearest_resistance − Close) / ATR_D1
```

Qualification: `headroom ≥ 1.0` required; `≥ 2.0` scores full points. A stock at all-time highs
has infinite headroom → full points.

## 8. Laguerre RSI (LRSI) — dip timing

Used exactly as in "Don't Overthink This": dip-entry alerts on M5.

```
gamma = 0.5   (M5 default)
L0 = (1−gamma)·price + gamma·L0[1]
L1 = −gamma·L0 + L0[1] + gamma·L1[1]
L2 = −gamma·L1 + L1[1] + gamma·L2[1]
L3 = −gamma·L2 + L2[1] + gamma·L3[1]
CU = Σ positive steps, CD = Σ negative steps (over L0..L3 cascade)
LRSI = CU / (CU + CD)          # 0..1, use ×100 for 0..100
```

Signals: strong stock `LRSI > 80`; dip-reset alert when `LRSI < 20` then re-crosses `> 20`;
exit-evaluation alert when `> 80` then falls `< 80`.

## 9. Algorithmic trendlines (SPY, M5)

Used by the bias engine for the "A Simple Strategy" trigger (SPY breaches its intraday
down-trendline → go long the RS list).

- Pivot highs/lows on M5 with strength 3 (3 bars each side).
- Down-trendline: line through the two most recent descending pivot highs (≥ 6 bars apart);
  refit as new pivots print. Up-trendline: mirrored on ascending pivot lows.
- Breach: M5 **close** beyond the line by ≥ `0.05 × ATR_M5(14)` (close-through, not wick-through).

## 10. Candle-structure metrics (SPY, M5 and D1)

Inputs to trend/chop classification (03):

```
stacked(n)   = count of consecutive same-direction closes; "stacked candles" when ≥ 3
               with bodies ≥ 60% of range and RVOL ≥ 1.2          # directional conviction
overlap(t)   = intersection(range(t), range(t−1)) / range(t)      # mixed overlapping candles
chop_ratio   = mean(overlap, 12 bars); ≥ 0.6 with alternating colors → chop
follow_through(D1) = after a D1 breakout candle, next 2–3 sessions close above its midpoint
                     on RVOL ≥ 1.0                                 # real-vs-fake breakout test
```
