import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from rs_spy.indicators.rrs import expected_price_change, power_index, price_change, rolling_rrs, rrs


# Golden fixtures taken directly from the worked examples in
# documents/A-New-Measure-of-Relative-Strength.md (window=1, one "hour" of
# price change):
#
# Example 1: SPY drops $2/hr (ATR_M=.50 -> PowerIndex=-4.0). Stock has
# ATR_S=.20 (expected change = -4.0*.20 = -.80) but only drops $.20 (holds up
# well) -> RRS = (-.20 - -.80) / .20 = 3.0
#
# Example 2: SPY rises $1.50/hr (ATR_M=.50 -> PowerIndex=3.0). Stock has
# ATR_S=.20 (expected change = 3.0*.20 = .60) but only rises $.20 (weak) ->
# RRS = (.20 - .60) / .20 = -2.0
def _series(vals):
    return pd.Series(vals, dtype=float)


def test_power_index_and_expected_pc_golden():
    bench_close = _series([370.0, 368.0])  # drops $2
    bench_atr = _series([np.nan, 0.50])
    pi = power_index(bench_close, bench_atr, window=1)
    assert pi.iloc[1] == -4.0

    stock_atr = _series([np.nan, 0.20])
    epc = expected_price_change(pi, stock_atr)
    assert epc.iloc[1] == -0.80


def test_rrs_golden_example_1_strength():
    stock_close = _series([100.0, 99.80])  # drops $.20
    stock_atr = _series([np.nan, 0.20])
    bench_close = _series([370.0, 368.0])  # drops $2.00
    bench_atr = _series([np.nan, 0.50])

    result = rrs(stock_close, stock_atr, bench_close, bench_atr, window=1)
    assert result.iloc[1] == pytest.approx(3.0)


def test_rrs_golden_example_2_weakness():
    stock_close = _series([100.0, 100.20])  # rises $.20
    stock_atr = _series([np.nan, 0.20])
    bench_close = _series([370.0, 371.50])  # rises $1.50
    bench_atr = _series([np.nan, 0.50])

    result = rrs(stock_close, stock_atr, bench_close, bench_atr, window=1)
    assert result.iloc[1] == pytest.approx(-2.0)


def test_price_change_window():
    close = _series([10, 11, 13, 12, 15])
    pc = price_change(close, window=2)
    np.testing.assert_allclose(pc.to_numpy(), [np.nan, np.nan, 3, 1, 2])


def test_rolling_rrs_is_simple_moving_average():
    rrs_series = _series([1.0, -0.5, 2.0, 0.5, -1.0, 3.0])
    result = rolling_rrs(rrs_series, window=3)
    expected = [np.nan, np.nan, (1.0 - 0.5 + 2.0) / 3, (-0.5 + 2.0 + 0.5) / 3,
                (2.0 + 0.5 - 1.0) / 3, (0.5 - 1.0 + 3.0) / 3]
    np.testing.assert_allclose(result.to_numpy(), expected)


def _naive_rolling_mean(values: list[float], window: int) -> list[float]:
    """Independently-written reference implementation (plain Python loop,
    not touching pandas .rolling) to cross-check rolling_rrs's vectorized
    implementation -- a metamorphic test per the plan's testing strategy."""
    out = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(float("nan"))
        else:
            out.append(sum(values[i - window + 1 : i + 1]) / window)
    return out


@given(
    values=st.lists(st.floats(min_value=-10, max_value=10, allow_nan=False), min_size=5, max_size=40),
    window=st.integers(min_value=1, max_value=5),
)
def test_rolling_rrs_matches_naive_reference(values, window):
    series = _series(values)
    vectorized = rolling_rrs(series, window).to_numpy()
    naive = _naive_rolling_mean(values, window)
    np.testing.assert_allclose(vectorized, naive, rtol=1e-9, atol=1e-12)


@given(
    stock_close=st.lists(st.floats(min_value=50, max_value=200), min_size=10, max_size=30),
    bench_close=st.lists(st.floats(min_value=50, max_value=200), min_size=10, max_size=30),
    stock_atr_base=st.floats(min_value=0.1, max_value=5),
    bench_atr_base=st.floats(min_value=0.1, max_value=5),
    k_stock=st.floats(min_value=0.5, max_value=5),
    k_bench=st.floats(min_value=0.5, max_value=5),
)
def test_rrs_scale_invariance(stock_close, bench_close, stock_atr_base, bench_atr_base, k_stock, k_bench):
    n = min(len(stock_close), len(bench_close))
    sc = _series(stock_close[:n])
    bc = _series(bench_close[:n])
    sa = _series([stock_atr_base] * n)
    ba = _series([bench_atr_base] * n)

    baseline = rrs(sc, sa, bc, ba, window=3)
    scaled = rrs(sc * k_stock, sa * k_stock, bc * k_bench, ba * k_bench, window=3)

    valid = baseline.notna()
    np.testing.assert_allclose(
        scaled[valid].to_numpy(), baseline[valid].to_numpy(), rtol=1e-6, atol=1e-9
    )
