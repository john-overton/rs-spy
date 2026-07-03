import pandas as pd

from rs_spy.indicators.sma_stack import ABOVE_ALL, BELOW_ALL, MIXED, sma_stack


def _df(closes):
    return pd.DataFrame({"close": closes})


def test_above_all_when_close_exceeds_every_sma():
    # periods=(2,3,4): sma2=13.5 sma3=13.0 sma4=12.5 at idx4, close=14
    df = _df([10, 11, 12, 13, 14])
    result = sma_stack(df, periods=(2, 3, 4))
    assert result.iloc[4] == ABOVE_ALL
    assert result.iloc[3] == ABOVE_ALL  # close=13 > sma2=12.5, sma3=12, sma4=11.5


def test_below_all_when_close_under_every_sma():
    # sma2=10.0 sma3=10.667 sma4=10.75 at idx4, close=9
    df = _df([10, 11, 12, 11, 9])
    result = sma_stack(df, periods=(2, 3, 4))
    assert result.iloc[4] == BELOW_ALL


def test_mixed_when_close_equals_one_sma():
    # idx3: close=11, sma2=11.5 (above), sma3=11.333 (above), sma4=11.0 (equal)
    df = _df([10, 11, 12, 11, 9])
    result = sma_stack(df, periods=(2, 3, 4))
    assert result.iloc[3] == MIXED


def test_none_when_insufficient_history():
    df = _df([10, 11, 12])
    result = sma_stack(df, periods=(2, 3, 4))
    assert result.iloc[0] is None
    assert result.iloc[1] is None
    assert result.iloc[2] is None  # sma4 still NaN


def test_categories_mutually_exclusive_and_exhaustive():
    import numpy as np

    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = _df(closes)
    result = sma_stack(df, periods=(5, 10, 20))
    valid = result.dropna()
    assert set(valid.unique()) <= {ABOVE_ALL, BELOW_ALL, MIXED}
