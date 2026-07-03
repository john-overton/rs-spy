import pandas as pd

from rs_spy.data.session import filter_rth, rth_mask


def test_rth_mask_excludes_premarket_and_afterhours():
    # 2024-03-01 is EST (UTC-5): RTH is 14:30-20:59 UTC (09:30-15:59 ET).
    index = pd.to_datetime(
        [
            "2024-03-01 12:00",  # 07:00 ET, premarket
            "2024-03-01 14:29",  # 09:29 ET, one minute before open
            "2024-03-01 14:30",  # 09:30 ET, open
            "2024-03-01 18:00",  # 13:00 ET, midday
            "2024-03-01 20:59",  # 15:59 ET, last RTH minute
            "2024-03-01 21:00",  # 16:00 ET, after close
            "2024-03-01 23:00",  # 18:00 ET, afterhours
        ],
        utc=True,
    )
    mask = rth_mask(index)
    assert mask.to_list() == [False, False, True, True, True, False, False]


def test_rth_mask_handles_dst_shift():
    # 2024-06-03 is EDT (UTC-4): RTH is 13:30-19:59 UTC (09:30-15:59 ET).
    index = pd.to_datetime(["2024-06-03 13:29", "2024-06-03 13:30", "2024-06-03 19:59"], utc=True)
    assert rth_mask(index).to_list() == [False, True, True]


def test_rth_mask_accepts_naive_index_as_utc():
    index = pd.to_datetime(["2024-03-01 12:00", "2024-03-01 14:30"])
    assert index.tz is None
    assert rth_mask(index).to_list() == [False, True]


def test_filter_rth_drops_non_rth_rows():
    index = pd.to_datetime(["2024-03-01 12:00", "2024-03-01 14:30", "2024-03-01 21:30"], utc=True)
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=index)
    result = filter_rth(df)
    assert result["close"].tolist() == [2.0]
