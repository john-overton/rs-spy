import numpy as np
import pandas as pd

from rs_spy.data.resample import align_causal, align_daily_to_intraday, resample_ohlcv


def _m1_session(start="2024-06-03 13:30", n=20, base=100.0):
    # 2024-06-03 is EDT: 13:30 UTC = 09:30 ET (session open).
    index = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    closes = base + np.arange(n) * 0.1
    return pd.DataFrame(
        {
            "open": closes - 0.05,
            "high": closes + 0.05,
            "low": closes - 0.05,
            "close": closes,
            "volume": np.full(n, 100.0),
        },
        index=index,
    )


def test_resample_ohlcv_aggregates_5min_buckets():
    df = _m1_session(n=10)  # 09:30..09:39 ET == 13:30..13:39 UTC (index stays UTC, no ET conversion)
    out = resample_ohlcv(df, "5min")
    assert list(out.index.strftime("%H:%M")) == ["13:35", "13:40"]
    first = out.iloc[0]
    assert first["open"] == df["open"].iloc[0]
    assert first["close"] == df["close"].iloc[4]
    assert first["high"] == df["high"].iloc[0:5].max()
    assert first["low"] == df["low"].iloc[0:5].min()
    assert first["volume"] == 500.0


def test_resample_ohlcv_drops_empty_buckets():
    df = _m1_session(n=5)  # only 09:30..09:34, one 5min bucket
    out = resample_ohlcv(df, "5min")
    assert len(out) == 1  # no NaN row for buckets with zero source bars


def test_resample_ohlcv_h1_from_m5_first_bucket_is_partial():
    m1 = _m1_session(n=65)  # 09:30..10:34
    m5 = resample_ohlcv(m1, "5min")
    h1 = resample_ohlcv(m5, "1h")
    # first H1 bucket [09:00,10:00) only has 09:30-09:55 data (6 M5 bars)
    assert h1.iloc[0]["open"] == m5["open"].iloc[0]
    assert h1.iloc[0]["close"] == m5.loc[m5.index < pd.Timestamp("2024-06-03 14:00", tz="UTC"), "close"].iloc[-1]


def test_align_causal_forward_fills_without_lookahead():
    source = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.to_datetime(["2024-06-03 14:00", "2024-06-03 15:00", "2024-06-03 16:00"], utc=True),
    )
    target = pd.to_datetime(
        ["2024-06-03 13:59", "2024-06-03 14:00", "2024-06-03 14:30", "2024-06-03 16:30"], utc=True
    )
    result = align_causal(source, target)
    assert result.iloc[0] is None or pd.isna(result.iloc[0])  # before any source value
    assert result.iloc[1] == 1.0  # exact match at 14:00 -- known at that instant, not lookahead
    assert result.iloc[2] == 1.0  # 14:30 still only knows the 14:00 value
    assert result.iloc[3] == 3.0  # past 16:00, knows the latest


def test_resample_ohlcv_closed_right_fixes_hour_boundary_misattribution():
    # Regression test: resample_ohlcv's output (e.g. M5 bars) is
    # close-labeled -- a bar timestamped 10:00 represents the interval
    # [09:55, 10:00), i.e. it belongs to the hour ENDING at 10:00, not the
    # hour starting at 10:00. Re-resampling such already-close-labeled data
    # up to H1 with the default closed="left" mis-buckets that boundary bar
    # into the NEXT hour. closed="right" fixes it.
    #
    # Build 5-min bars where every bar up to and including 10:00 has price
    # 1.0 (belongs to the 09:00-10:00 hour) and every bar after 10:00 has
    # price 2.0 (belongs to the 10:00-11:00 hour).
    index = pd.date_range("2024-06-03 09:35", "2024-06-03 11:00", freq="5min", tz="UTC")
    price = np.where(index <= pd.Timestamp("2024-06-03 10:00", tz="UTC"), 1.0, 2.0)
    m5 = pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": np.full(len(index), 100.0)},
        index=index,
    )

    h1_buggy = resample_ohlcv(m5, "1h")  # default closed="left"
    h1_fixed = resample_ohlcv(m5, "1h", closed="right")

    # Buggy (closed="left"): the 10:00 bar (price 1.0, truly part of the
    # 09:00-10:00 hour) leaks into the [10:00,11:00) bucket, contaminating
    # its "open" (first bar) with the previous hour's price.
    second_bucket_buggy = h1_buggy.loc[pd.Timestamp("2024-06-03 11:00", tz="UTC")]
    assert second_bucket_buggy["open"] == 1.0  # demonstrates the misattribution

    # Fixed (closed="right"): the 10:00 bar correctly lands in the
    # (09:00,10:00] bucket, so the second hour's "open" is untainted.
    second_bucket_fixed = h1_fixed.loc[pd.Timestamp("2024-06-03 11:00", tz="UTC")]
    assert second_bucket_fixed["open"] == 2.0  # correct hour attribution


def test_align_daily_to_intraday_uses_prior_session_by_default():
    daily = pd.Series([10.0, 20.0, 30.0], index=pd.to_datetime(["2024-06-03", "2024-06-04", "2024-06-05"]))
    intraday_index = pd.to_datetime(["2024-06-04 14:00", "2024-06-05 14:00"], utc=True)
    result = align_daily_to_intraday(daily, intraday_index)
    # session 06-04's intraday bars see 06-03's value (10.0); 06-05 sees 06-04's (20.0)
    assert result.tolist() == [10.0, 20.0]
