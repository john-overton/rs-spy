"""Cross-timeframe bar aggregation + causal alignment.

The warehouse stores raw 1-minute bars (see data/loader.py::load_minute_bars).
The spec's "M5" cadence (algo-spec/02-04) means true 5-minute bars -- RRS's
L=12 window means "1 hour" only if each bar is 5 minutes, LRSI's gamma=0.5
default is calibrated for 5-minute decay, trendline pivot spacing assumes
5-minute bars. `resample_ohlcv` builds those 5-minute (and, from there,
hourly) bars by aggregation. VWAP is the one indicator the spec explicitly
computes from 1-minute bars (02 §3) rather than M5 bars.

`align_causal`/`align_daily_to_intraday` broadcast a slower/sparser series
(H1 ATR, D1 features, 1-min VWAP/RVOL) onto a faster/denser target index
(typically M5 bar timestamps) via forward-fill: each target timestamp sees
the most recent source value *at or before* it, never a later one. This is
the same causal-alignment shape used three times in the M5 build (H1->M5 ATR
for RRS, D1->M5 for ha_cont_d1/sma_stack/regime_d1/etc., M1->M5 for
VWAP/RVOL) -- one generic primitive rather than three bespoke ones.
"""
import pandas as pd

_OHLCV_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, freq: str, closed: str = "left") -> pd.DataFrame:
    """Aggregate OHLCV bars up to a coarser `freq` (pandas offset alias, e.g.
    "5min", "1h"). Buckets are labeled by their right (close) edge -- e.g.
    resample_ohlcv(m1, "5min") on RTH data starting 09:30 produces a bar
    labeled 09:35 covering [09:30, 09:35). Buckets with zero source bars
    (market closed overnight, or a coverage gap in a thin symbol's minute
    data) are dropped rather than left as a NaN row.

    `closed` controls which side of each bucket is inclusive and defaults to
    "left" (bucket = [left, right)), which is correct when `df` is
    open-labeled source data -- e.g. raw M1 bars, whose own timestamp is a
    bar's start, being resampled up to M5. Pass `closed="right"` (bucket =
    (left, right]) when `df` is itself already close-labeled -- e.g.
    resampling this function's own M5 output up to H1 -- so a source bar
    timestamped exactly on a bucket boundary (representing the interval
    ENDING at that timestamp) is grouped into the bucket it actually
    belongs to rather than the next one."""
    cols = list(_OHLCV_AGG)
    agg = df[cols].resample(freq, label="right", closed=closed).agg(_OHLCV_AGG)
    return agg.dropna(subset=["open"])


def align_causal(source: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill `source` onto `target_index`: target[t] = the most recent
    source value at a timestamp <= t (NaN/None before the first source
    value). `source.index` and `target_index` must already be
    timezone-comparable (both tz-aware in the same tz, or both naive)."""
    combined = source.index.union(target_index)
    return source.reindex(combined).ffill().reindex(target_index).rename(source.name)


def align_daily_to_intraday(daily: pd.Series, intraday_index: pd.DatetimeIndex, shift: int = 1) -> pd.Series:
    """Broadcast a D1-indexed series onto an intraday (M1/M5) index,
    shifting by `shift` sessions first (default 1: yesterday's value) so
    that a session's own intraday bars never see that same session's D1
    row -- matching 03 §2's "pre-open pass" (today's own D1 bar isn't
    closed yet during today's session). `daily.index` is assumed
    date-normalized and naive, as data.loader.load_daily_bars produces; it
    is localized to `intraday_index`'s timezone before aligning."""
    shifted = daily.shift(shift)
    if intraday_index.tz is not None and shifted.index.tz is None:
        shifted = shifted.copy()
        shifted.index = shifted.index.tz_localize(str(intraday_index.tz))
    return align_causal(shifted, intraday_index)
