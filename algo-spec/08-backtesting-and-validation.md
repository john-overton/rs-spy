# 08 — Backtesting and Validation

The wiki warns that naive backtests of home-grown edges mislead ("Back-test all you want…
you are going to wind up shaking your head"). The mitigation is not to skip validation but to
validate the *documented* edge faithfully and attack our own results.

## 1. Data and simulation requirements

- ≥ 2 years of 1-min bars for the full universe + SPY/QQQ, split-adjusted, survivorship-bias
  free (include delisted symbols that met universe rules at the time).
- Point-in-time reference data: float, ADV, sector, earnings dates as known on each day.
- Bar-close decision timing exactly as in live (signals on closed M5 bars only), fills modeled
  at next-bar prices with spread + slippage model (½ spread + 2 bps default; sensitivity sweep).
- Regime coverage: the window must include at least one trending-up, one trending-down, and
  one extended chop period (e.g. 2022 bear + 2023 grind + a chop stretch).

## 2. Primary metrics

| Metric | Target / expectation |
|--------|----------------------|
| Win rate | ~70%+ (OneOption graduation bar is 75% for discretionary traders) |
| Profit factor | ≥ 1.8 |
| Average win / average loss | ≥ 1.0 (with 70% win rate this is strongly profitable) |
| Max drawdown | consistent with 07 limits; daily loss limit binding < 5% of sessions |
| Trades/day | ~3–8 ("at least 5 really good trades throughout the day" on trigger days) |

## 3. Required studies

### 3.1 Rule-count ablation ("Keeping it Really Simple" §coding exercise)

Re-run the backtest with each hard gate (market bias, VWAP, HA continuation, SMA stack)
individually disabled and score every historical trade by how many of the four rules it
satisfied. **Expected result: win rate and expectancy increase monotonically with rules
satisfied.** If a gate does not improve results, the implementation of that gate is suspect —
investigate before deleting the rule.

### 3.2 Walk-away analysis (wiki's diagnostic)

For every entry signal (including ones skipped by risk limits), record the price path for the
rest of the session had it been held: max favorable/adverse excursion. Validates that exits —
not picks — are where P&L is won or lost, and calibrates profit-take/trail parameters.

### 3.3 RRS parameter sensitivity

Sweep: window `L ∈ {6, 12, 18}`, ATR basis (H1-50 vs M5-600), qualification threshold
`{0.75, 1.0, 1.5}`, rolling vs raw RRS. The edge should be broad and stable across the sweep;
a sharp peak at one setting means overfitting.

### 3.4 Bias-engine confusion matrix

Score each session's bias calls against realized SPY forward returns per bar
(the "How To Read The Market" journaling exercise, automated). Track separately for bull and
bear calls — the wiki author found his bearish reads lagged his bullish reads; expect asymmetry
and calibrate the NEUTRAL band accordingly.

### 3.5 Time-of-day and regime slicing

P&L by entry hour, by `regime_d1`, by bias tier, long vs short. Shorts must justify their
existence separately (06 is disabled by default until this slice is positive).

## 4. Promotion pipeline

1. **Backtest** passes §2 targets across all regime slices.
2. **Paper trading** ≥ 2 months live-data shadow, matching backtest expectancy within noise
   (mirrors the wiki's "two straight months" graduation standard).
3. **Live, minimum size** (fixed 1R = 0.1%) ≥ 1 month; slippage/fill model recalibrated
   against reality.
4. **Live, target size**, shorts still off. Shorts enabled only after their own paper cycle.

## 5. Ongoing monitoring

- Daily automated journal: every signal, gate state, score components, and exit reason —
  the algorithmic equivalent of the wiki's trade journaling discipline.
- Weekly walk-away rerun on the last month of live signals.
- Drift alarms: RRS distribution, gate pass-rates, and win rate tracked with control bands;
  breach → reduce size automatically and flag for review.
