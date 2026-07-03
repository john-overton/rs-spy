"""Laguerre RSI (LRSI) -- dip timing on M5. algo-spec/02 §8.

Ehlers' 4-stage cascade filter has genuine inter-bar *and* inter-stage
dependencies (each stage's value at t depends on its own previous value AND
the previous stage's current and previous values) that don't reduce to a
pandas rolling/ewm primitive. Implemented as a documented, deliberately
non-vectorized loop -- performance is a non-issue at this data scale, per
the plan's stated exception (mirrors trendlines.py's pivot-refit loop).

All four stages seed at 0.0 (not `price[0]`), so there's a warmup transient
of roughly `1/(1-gamma)` bars before the cascade tracks price -- same
character as any other recursive/EWM-style filter's warmup (e.g. Wilder
ATR's seeded first value). A constant price series converges to exactly
LRSI=50 once the transient decays (L0=L1=L2=L3=price is the fixed point of
the recursion), which is what the golden test below checks.
"""
import numpy as np
import pandas as pd


def laguerre_rsi(price: pd.Series, gamma: float = 0.5) -> pd.Series:
    """Returns LRSI on a 0-100 scale (spec's 0-1 CU/(CU+CD) times 100)."""
    n = len(price)
    p = price.to_numpy(dtype=float)
    l0 = np.zeros(n)
    l1 = np.zeros(n)
    l2 = np.zeros(n)
    l3 = np.zeros(n)
    lrsi = np.full(n, np.nan)

    prev_l0 = prev_l1 = prev_l2 = prev_l3 = 0.0
    for t in range(n):
        l0[t] = (1 - gamma) * p[t] + gamma * prev_l0
        l1[t] = -gamma * l0[t] + prev_l0 + gamma * prev_l1
        l2[t] = -gamma * l1[t] + prev_l1 + gamma * prev_l2
        l3[t] = -gamma * l2[t] + prev_l2 + gamma * prev_l3

        cu = 0.0
        cd = 0.0
        if l0[t] >= l1[t]:
            cu += l0[t] - l1[t]
        else:
            cd += l1[t] - l0[t]
        if l1[t] >= l2[t]:
            cu += l1[t] - l2[t]
        else:
            cd += l2[t] - l1[t]
        if l2[t] >= l3[t]:
            cu += l2[t] - l3[t]
        else:
            cd += l3[t] - l2[t]

        lrsi[t] = (cu / (cu + cd)) if (cu + cd) != 0 else 0.5
        prev_l0, prev_l1, prev_l2, prev_l3 = l0[t], l1[t], l2[t], l3[t]

    return pd.Series(lrsi * 100, index=price.index, name="lrsi")
