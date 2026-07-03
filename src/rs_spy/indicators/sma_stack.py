"""D1 SMA stack classifier. algo-spec/02-indicators-and-calculations.md §6."""
import pandas as pd

PERIODS = (50, 100, 200)

ABOVE_ALL = "ABOVE_ALL"
BELOW_ALL = "BELOW_ALL"
MIXED = "MIXED"


def smas(df: pd.DataFrame, periods: tuple[int, ...] = PERIODS) -> pd.DataFrame:
    return pd.DataFrame({f"sma{p}": df["close"].rolling(p).mean() for p in periods}, index=df.index)


def sma_stack(df: pd.DataFrame, periods: tuple[int, ...] = PERIODS) -> pd.Series:
    sma_df = smas(df, periods)
    close = df["close"]

    above = pd.concat([close > sma_df[f"sma{p}"] for p in periods], axis=1).all(axis=1)
    below = pd.concat([close < sma_df[f"sma{p}"] for p in periods], axis=1).all(axis=1)
    has_nan = sma_df.isna().any(axis=1)

    # Built directly as an object-dtype Series (rather than via np.where) to
    # avoid numpy silently coercing None when mixed with a fixed-width
    # unicode string array.
    result = pd.Series(MIXED, index=df.index, dtype=object)
    result[above] = ABOVE_ALL
    result[below] = BELOW_ALL
    result[has_nan] = None
    return result.rename("sma_stack")
