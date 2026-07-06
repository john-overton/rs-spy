# The Predictive Engine

## The idea

A next-state predictor over market states, trained on years of data, outputting a probability over what comes next. The LLM analogy is exact: given the preceding sequence of states, learn the distribution over the next state. Not "predict price from raw candles," which is the version that fails.

## Build order (transparent-first)

### 1. Tokenization is the whole game

Convert each candle-plus-volume into a discrete state. Too rich a description and every state is unique (the sparsity wall, "76% from a sample of one"). Too coarse and states recur but carry no signal. Finding the granularity that's both recurring and predictive *is* the project, and it's found empirically, not by formula. This is where to spend the time.

### 2. The per-candle bias score must be scale-free

Raw price-change times volume breaks across names (Apple $0.50 vs Micron $50 on the same volume). The fix is two normalizations, not float multiplied in:

- Price move in the stock's *own* volatility units (ATR multiples or percent), so "big" means big for that stock.
- Volume relative to the stock's *own* norm (average daily volume, cleaner and more responsive than float; float only if a specific reason justifies maintaining that messier data).

Clean form, per name:

```
sign(price_change) x (price_change / ATR) x (volume / average_volume)
```

### 3. Aggregate a basket, not one stock

10-15 same-sector names so idiosyncratic noise cancels and the shared sector ebb-and-flow reinforces (same logic as breadth, scoped to a sector). Test two weightings:

- Dollar-volume (where the money is).
- Participation / equal-weight of each name's bias contribution (how broad the move is).

Divergence between the two is itself signal: money moving but breadth thin equals a fragile move, the "rally tiring" state.

### 4. Build a Markov chain first, not a neural net

Bucket the basket bias score into a handful of states (strong up, mild up, flat, mild down, strong down), then *count* transitions across 5 years: from each state, what fraction of history went to each next state. Normalize rows to percentages. That grid *is* the "76% chance of X next" machine, fully inspectable, no training, can't leak if time is ordered correctly.

Bake recent history into the state itself (last few candles + volume regime) to respect the fact that markets have some memory beyond one bar.

### 5. The Markov grid is a signal detector

If rows come back roughly uniform, the vocabulary has no edge and that's learned in a weekend. If rows are lopsided, real structure exists, and *that's* when a heavier sequence model (or a net that tunes the weights of a signal already proven) earns its place.

## Honest expectations

The number won't be 76%. Short-horizon markets are near-efficient, so real edges are more like 54/46 or 57/43, small but durable. A model claiming 76% is usually leaking or overfit.

Transition probabilities drift with regime, so retraining on a rolling window is likely necessary. That's the "gradients against past data" behavior already intuited.

## Where this stands

Architecture is settled, the scale-free scoring problem is solved, the basket and weighting approach is set. The immediate next fork is the tokenization scheme: turning that continuous basket score into the discrete state vocabulary the Markov grid runs on.
