import numpy as np
import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from rs_spy.indicators.atr import atr, true_range


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# Hand-computed golden fixture, n=3:
#   bar0: H=10 L=8  C=9   TR0 = H-L = 2 (no prior close)
#   bar1: H=11 L=9  C=10  TR1 = max(2, |11-9|, |9-9|) = 2
#   bar2: H=12 L=10 C=11  TR2 = max(2, |12-10|, |10-10|) = 2
#   bar3: H=9  L=7  C=8   TR3 = max(2, |9-11|, |7-11|) = 4
#   bar4: H=10 L=8  C=9   TR4 = max(2, |10-8|, |8-8|) = 2
#   bar5: H=11 L=9  C=10  TR5 = max(2, |11-9|, |9-9|) = 2
# seed (index 2) = mean(TR0..TR2) = 2.0
# ATR3 = 2.0*2/3 + 4/3   = 8/3
# ATR4 = ATR3*2/3 + 2/3  = 22/9
# ATR5 = ATR4*2/3 + 2/3  = 62/27
_GOLDEN_ROWS = [
    (0, 10, 8, 9),
    (0, 11, 9, 10),
    (0, 12, 10, 11),
    (0, 9, 7, 8),
    (0, 10, 8, 9),
    (0, 11, 9, 10),
]
_GOLDEN_TR = [2, 2, 2, 4, 2, 2]
_GOLDEN_ATR3 = [np.nan, np.nan, 2.0, 8 / 3, 22 / 9, 62 / 27]


def test_true_range_golden():
    df = _df(_GOLDEN_ROWS)
    tr = true_range(df)
    np.testing.assert_allclose(tr.to_numpy(), _GOLDEN_TR)


def test_atr_golden():
    df = _df(_GOLDEN_ROWS)
    result = atr(df, n=3)
    np.testing.assert_allclose(result.to_numpy(), _GOLDEN_ATR3, rtol=1e-9)


def test_atr_insufficient_history_is_nan():
    df = _df(_GOLDEN_ROWS[:2])
    result = atr(df, n=3)
    assert result.isna().all()


@given(
    st.lists(
        st.tuples(
            st.floats(min_value=50, max_value=150),
            st.floats(min_value=0.1, max_value=20),
        ),
        min_size=20,
        max_size=60,
    )
)
def test_atr_always_nonnegative(base_and_range):
    rows = []
    for base, rng in base_and_range:
        low = base
        high = base + rng
        close = base + rng / 2
        rows.append((base, high, low, close))
    df = _df(rows)
    result = atr(df, n=5)
    valid = result.dropna()
    assert (valid >= 0).all()
