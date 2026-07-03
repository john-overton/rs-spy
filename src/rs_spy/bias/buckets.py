"""Shared bias-bucket vocabulary + score-to-bucket hysteresis smoothing.
algo-spec/03-market-bias-engine.md §3 (mapping table + hold-2-bars rule).

Used by both the D1 walking-skeleton engine (bias/engine_d1.py) and the full
M5 engine (bias/engine.py) -- the thresholds and hold-count are identical at
both cadences (spec doesn't vary them by timeframe); only the meaning of "a
bar" differs, which callers encode simply by passing in a D1- or M5-indexed
score series.
"""
import pandas as pd

STRONG_BULL = "STRONG_BULL"
BULL = "BULL"
NEUTRAL = "NEUTRAL"
BEAR = "BEAR"
STRONG_BEAR = "STRONG_BEAR"

LONG_TRIGGER = "LONG_TRIGGER"
SHORT_TRIGGER = "SHORT_TRIGGER"
NO_TRIGGER = "NONE"

BULL_TH = 25.0
STRONG_BULL_TH = 60.0
BEAR_TH = -25.0
STRONG_BEAR_TH = -60.0
HOLD_BARS = 2


def apply_hysteresis(smoothed: pd.Series, hold_bars: int = HOLD_BARS) -> pd.Series:
    """03 §3: leaving NEUTRAL requires the score to cross +-25 and hold for
    `hold_bars` consecutive bars ("confirm rather than anticipate"); falling
    back into NEUTRAL, or moving between a side's STRONG/non-STRONG tier
    while already on that side, is immediate (no hold requirement)."""
    n = len(smoothed)
    bucket: list[str | None] = [None] * n
    state = NEUTRAL
    pending_dir: str | None = None
    pending_count = 0

    for i in range(n):
        s = smoothed.iat[i]
        if pd.isna(s):
            bucket[i] = None
            continue

        if state == NEUTRAL:
            if s >= BULL_TH or s <= BEAR_TH:
                direction = BULL if s >= BULL_TH else BEAR
                if pending_dir == direction:
                    pending_count += 1
                else:
                    pending_dir, pending_count = direction, 1
                if pending_count >= hold_bars:
                    if direction == BULL:
                        state = STRONG_BULL if s >= STRONG_BULL_TH else BULL
                    else:
                        state = STRONG_BEAR if s <= STRONG_BEAR_TH else BEAR
                    pending_dir, pending_count = None, 0
            else:
                pending_dir, pending_count = None, 0
        elif state in (BULL, STRONG_BULL):
            state = NEUTRAL if s < BULL_TH else (STRONG_BULL if s >= STRONG_BULL_TH else BULL)
        else:  # BEAR, STRONG_BEAR
            state = NEUTRAL if s > BEAR_TH else (STRONG_BEAR if s <= STRONG_BEAR_TH else BEAR)

        bucket[i] = state

    return pd.Series(bucket, index=smoothed.index, name="bias")
