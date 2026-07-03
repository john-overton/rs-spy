# RS-SPY Intraday Algorithm Specification

Specification for an intraday trading system built on the **Relative Strength / Relative
Weakness (RS/RW) methodology** documented in the r/RealDayTrading wiki (`../documents/`) and the
OneOption "Market First" methodology (https://oneoption.com/intro-series/our-trading-methodology/).

## Core thesis

1. **Institutions move the market, not retail.** RS/RW is a detector of concentrated
   institutional buying or selling in a single equity. That is the edge — we follow big money,
   we do not predict.
2. **Market First.** Roughly 75–80% of stocks follow SPY. The market read (bias) gates every
   trade. The system never fights the market: market up → longs only, market down → shorts
   only, market undecided → flat.
3. **Do not trade the index itself.** The wiki is explicit that there is no edge trading SPY/QQQ
   directly ("Trading SPY/QQQ — Should You Do It?" → No). SPY/QQQ are the *benchmark and the
   timing signal*, not the traded instrument. The edge is the proportional outperformance of an
   RS stock vs. the index in all three scenarios (market up / flat / down).
4. **Confirm, don't anticipate.** Enter after institutional activity is visible (RS/RW holding,
   volume, follow-through), accepting a later entry in exchange for a much higher win rate.
5. **Stack the checklist.** The wiki's "Keeping it Really Simple" rules show win rate rises
   monotonically with the number of conditions satisfied. The system encodes this as a
   weighted score with hard gates for the non-negotiable rules.

## System decomposition

The system is three cooperating engines plus a risk layer:

```
                 ┌────────────────────────┐
 SPY/QQQ data ──▶│ 1. Market Bias Engine  │──── bias ∈ {STRONG_BULL … STRONG_BEAR} ─┐
                 └────────────────────────┘                                          │
                 ┌────────────────────────┐                                          ▼
 Universe data ─▶│ 2. Stock Selection     │── ranked RS list (longs)   ┌──────────────────────┐
                 │    Engine (RS/RW scan) │── ranked RW list (shorts)─▶│ 3. Trade Engine      │
                 └────────────────────────┘                            │  (entry/exit/alerts) │
                 ┌────────────────────────┐                            └──────────┬───────────┘
                 │ 4. Risk Manager        │◀──────────────────────────────────────┘
                 └────────────────────────┘   sizing, stops, time windows, kill switches
```

## Document index

| File | Contents |
|------|----------|
| [01-data-requirements.md](01-data-requirements.md) | Source data, feeds, universe definition, storage schemas |
| [02-indicators-and-calculations.md](02-indicators-and-calculations.md) | All formulas: RRS, rolling RRS, ATR, VWAP, relative volume, HA continuation, LRSI, SMA stack, trendlines |
| [03-market-bias-engine.md](03-market-bias-engine.md) | SPY/QQQ market read: bias states, scoring, trendline-breach timing signal, breakout real/fake test |
| [04-stock-selection-engine.md](04-stock-selection-engine.md) | Universe filters, RS/RW ranking, D1 alignment, composite score, weights |
| [05-long-bias-algo.md](05-long-bias-algo.md) | Long algorithm: gates, entry triggers, exits |
| [06-short-bias-algo.md](06-short-bias-algo.md) | Short algorithm: gates, entry triggers, exits |
| [07-risk-management.md](07-risk-management.md) | Position sizing, stops, time filters, portfolio limits, kill switches |
| [08-backtesting-and-validation.md](08-backtesting-and-validation.md) | How to validate: walk-away analysis, rule-count ablation, metrics |

## Non-goals

- No low-float gapper / momentum trading in the first 30 minutes (explicitly out of scope per
  "Simple and Effective Day Trading Method").
- No overnight positions in v1 (day-trade flat by close; swing extension noted as future work).
- No options execution in v1 — signals are expressed in shares; an options layer (ITM calls/puts,
  lottos) can be added once the share-based system validates at a ~75% win rate, matching the
  OneOption graduation criterion.

## Source material

- r/RealDayTrading wiki posts by u/HSeldon2020 (H.S.) and u/OptionStalker (Pete) in `../documents/`
- OneOption methodology: https://oneoption.com/intro-series/our-trading-methodology/
- Community RRS implementations (e.g. TradingView "[#ps #mft] RDT's Real Relative Strength"):
  `RRS = (PC − expectedPC) / ATR`, `expectedPC = (mktPC / mktATR) × ATR`
