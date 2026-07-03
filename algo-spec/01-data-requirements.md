# 01 — Source Data Requirements

## 1. Instruments

| Group | Symbols | Purpose |
|-------|---------|---------|
| Benchmarks | SPY (primary), QQQ (secondary) | Market bias, RRS denominator, timing signals |
| Trade universe | ~800–1,500 liquid US equities (see §4) | RS/RW candidates |
| Optional internals | $TICK, $ADD, ES futures, VIX | Confirmation inputs to the bias engine (v2) |

SPY is the default benchmark for every stock ("using SPY as your benchmark is even more
successful than using QQQ or Sector Indexes"). QQQ is used as the benchmark **only** for the
bias engine's tech read and, optionally, as an alternate denominator for mega-cap tech names —
if used, a stock's RS must hold against *both* SPY and QQQ to qualify.

## 2. Market data feeds

### 2.1 Real-time (required)

| Data | Granularity | Latency need | Notes |
|------|-------------|--------------|-------|
| OHLCV bars | 1-minute (aggregate to 5-min) | < 5 s | Full universe + SPY/QQQ. 1-min bars are the base unit; M5/M15/M30 are derived. |
| Cumulative session volume | per bar | < 5 s | For VWAP and relative volume |
| Previous close / session open | daily | at open | Gap calculations |

Trades/quotes tick data is **not** required — every calculation in this system operates on
1-minute or coarser bars. Suitable providers: Polygon.io, Databento, Alpaca (SIP), IQFeed.

Bars must be **regular-session** (09:30–16:00 ET) for VWAP and relative-volume math; keep
pre-market bars separately for gap context.

### 2.2 Historical (required)

| Data | Depth | Used for |
|------|-------|----------|
| Daily OHLCV | ≥ 300 trading days | D1 SMAs (50/100/200), D1 ATR, D1 RRS, HA continuation, support/resistance, average volume |
| 1-min / 5-min OHLCV | ≥ 30 trading days | Hourly ATR (ATR50 on H1), time-of-day volume curve for RVOL, warm-up of rolling RRS |

### 2.3 Reference data (required)

| Data | Refresh | Used for |
|------|---------|----------|
| Shares float | weekly | Exclude low-float gappers (float < 50 M excluded) |
| Average daily volume (ADV, 20-day) | daily | Universe filter |
| Sector / industry (GICS) | monthly | Sector-level RS context, position concentration limits |
| Earnings calendar | daily | Hard exclusion: no entries on a symbol reporting earnings same day or next pre-market |
| Halt/LULD status | real-time | Never enter a halted or recently-halted symbol |
| Corporate actions (splits/dividends) | daily | Price series adjustment |

### 2.4 Optional (v2 enhancements)

- News/catalyst feed (e.g. TradeXchange, Benzinga) — tag *why* a stock is RS/RW; the method
  works without it because "the chart shows what institutions are doing."
- Options chain snapshots — for an options execution layer later.
- Dark-pool / block prints — **not needed**; per the wiki, dark-pool imbalances are instantly
  reflected in price, which RRS already captures.

## 3. Derived data (computed, stored intraday)

Per symbol, updated on each 5-minute bar close (see 02 for formulas):

| Field | Description |
|-------|-------------|
| `rrs_m5` | Real Relative Strength, 12-bar rolling, M5 vs SPY |
| `rrs_d1` | Real Relative Strength, 5-day window, D1 vs SPY |
| `atr_h1_50` | ATR of hourly bars, 50 periods |
| `atr_d1_14` | Daily ATR, 14 periods |
| `vwap` | Session VWAP |
| `rvol` | Relative volume vs time-of-day-adjusted 20-day average |
| `sma_stack_d1` | Position vs 50/100/200 SMA (above all / mixed / below all) |
| `ha_cont_d1` | Heikin-Ashi continuation day count and direction |
| `lrsi_m5` | Laguerre RSI on M5 |
| `headroom` | Distance to nearest D1 resistance in units of D1 ATR (longs); to support (shorts) |
| `score_long`, `score_short` | Composite candidate scores (see 04) |

## 4. Trade universe definition

Rebuilt nightly. A symbol is in the universe iff **all**:

1. Primary US exchange listing, common stock or ADR (no ETFs, no warrants/units/SPAC shells).
2. Last close ≥ **$10.00** (wiki: "make sure they aren't low float gappers, or any stock under $10").
3. 20-day ADV ≥ **1,000,000 shares** and 20-day average dollar volume ≥ **$25 M**
   ("make sure the stocks have good volume").
4. Shares float ≥ **50 M**.
5. Not halted in the prior 5 sessions; not in bankruptcy/delisting process.
6. Optionable (preferred, not required in v1; required when the options layer is added).

Membership in the S&P 500 is **not** required — the wiki is explicit that candidates "do not
have to be in the S&P 500."

## 5. Clock and session handling

- All logic keyed to **US/Eastern** exchange time.
- Half days: scale time windows proportionally; no new entries in the final 30 minutes of any
  session.
- The system trades only regular sessions. FOMC days, CPI mornings, and other scheduled
  macro releases are handled by the bias engine's chop detection, plus a configurable
  no-trade blackout of ±15 min around scheduled releases.
