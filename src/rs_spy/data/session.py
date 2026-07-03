"""Regular-trading-hours (RTH) session filtering for intraday bars.

Alpaca's minute feed includes pre/post-market bars (confirmed against real
cached data -- e.g. a bar as early as 08:32 UTC, well before the 13:30/14:30
UTC open), which must be excluded before computing session-anchored signals
like VWAP (algo-spec/02 §3 anchors at 09:30) or RVOL. Filtering by ET
wall-clock time (not a fixed UTC time-of-day) is required because the
UTC offset for 09:30/16:00 ET shifts by an hour across the DST boundary.
"""
import pandas as pd

_RTH_START = pd.Timedelta(hours=9, minutes=30)  # 09:30 ET, session open
_RTH_END = pd.Timedelta(hours=15, minutes=59)  # 15:59 ET, last full RTH minute bar


def rth_mask(index: pd.DatetimeIndex) -> pd.Series:
    et = index.tz_localize("UTC").tz_convert("America/New_York") if index.tz is None else index.tz_convert(
        "America/New_York"
    )
    time_of_day = et - et.normalize()
    return pd.Series((time_of_day >= _RTH_START) & (time_of_day <= _RTH_END), index=index)


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    return df[rth_mask(df.index).to_numpy()]
