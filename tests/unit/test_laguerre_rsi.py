import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from rs_spy.indicators.laguerre_rsi import laguerre_rsi


# Hand-computed golden fixture, constant price=100, gamma=0.5, zero-seeded stages:
#   t=0: L0=50,     L1=-25,   L2=12.5,   L3=-6.25
#        CU=(50-(-25))+(12.5-(-6.25))=93.75, CD=(12.5-(-25))=37.5 -> LRSI=93.75/131.25=71.42857...
#   t=1: L0=75,     L1=0,     L2=-18.75, L3=18.75
#        CU=(75-0)+(0-(-18.75))=93.75, CD=(18.75-(-18.75))=37.5  -> LRSI=71.42857... (same ratio)
#   t=2: L0=87.5,   L1=31.25, L2=-25,    L3=3.125
#        CU=(87.5-31.25)+(31.25-(-25))=112.5, CD=(3.125-(-25))=28.125 -> LRSI=112.5/140.625=80.0
# Each L-stage individually converges toward the constant price (=100) as t
# grows, but at different rates (L0 fastest, L3 slowest) -- for many bars
# after a step to a new constant price, L0>=L1>=L2>=L3 holds with all-positive
# gaps, pinning LRSI at exactly 100 for an extended stretch. That's a real,
# intentional property of this recursive filter (slow to "forget" a move),
# not a bug -- see module docstring.
_GOLDEN_LRSI = [500 / 7, 500 / 7, 80.0]


def test_laguerre_rsi_golden():
    price = pd.Series([100.0] * 3)
    result = laguerre_rsi(price)
    np.testing.assert_allclose(result.to_numpy(), _GOLDEN_LRSI, rtol=1e-9)


def test_constant_price_eventually_pins_at_100():
    # Documents the slow-decay transient described above rather than
    # asserting the (much longer-horizon, effectively unreachable in
    # float64) LRSI=50 fixed point.
    price = pd.Series([100.0] * 15)
    result = laguerre_rsi(price)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_strong_uptrend_pushes_lrsi_high():
    price = pd.Series(np.linspace(100, 200, 60))
    result = laguerre_rsi(price)
    assert result.iloc[-1] > 80


def test_strong_downtrend_pushes_lrsi_low():
    price = pd.Series(np.linspace(200, 100, 60))
    result = laguerre_rsi(price)
    assert result.iloc[-1] < 20


@given(st.lists(st.floats(min_value=1, max_value=1000, allow_nan=False), min_size=5, max_size=80))
def test_lrsi_always_within_bounds(prices):
    result = laguerre_rsi(pd.Series(prices))
    assert result.between(0, 100).all()


