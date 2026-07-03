"""D1-adapted composite score. algo-spec/04-stock-selection-engine.md §4.

The full spec splits 100 points across 7 weighted components, with W1 (M5
Rolling RRS magnitude, 25 pts) requiring intraday data this milestone
doesn't have. That weight is folded into W2 (D1 RRS) for the D1 walking
skeleton, since D1 RRS is the only RS signal available at this cadence:

  W2 D1 RRS magnitude       45  (25 + the redistributed 20)
  W3 D1 chart quality       15  (HA continuation length)
  W4 divergence bonus       15  (stock moves against/independent of SPY)
  W5 volume                 10
  W6 headroom                10
  W7 consistency              5
  ------------------------------
  total                    100

score_long_m5/score_short_m5 below implement the full, un-redistributed 7-
weight table (04 §4) at M5 cadence, now that RollingRRS_M5 (W1) is actually
available. Two spec details are simplified, matching this project's existing
"document, don't silently approximate" norm (see gates.py, bias/engine_d1.py):
W3's "+3 if 8-EMA(D1) preserved on all pullbacks in window" sub-bonus is not
implemented (base HA-continuation-length scoring only). W7's "lowest tercile
of candidates" is cross-sectional (needs the whole candidate pool at once);
this reuses the same per-symbol continuous-std formula the D1 W7 already
uses, rather than introducing an unrelated cross-sectional ranking API here.
"""
import numpy as np
import pandas as pd

W2_D1_RRS = 45.0
W3_CHART_QUALITY = 15.0
W4_DIVERGENCE = 15.0
W5_VOLUME = 10.0
W6_HEADROOM = 10.0
W7_CONSISTENCY = 5.0


def _linear_score(value: pd.Series, low: float, high: float, weight: float) -> pd.Series:
    frac = ((value - low) / (high - low)).clip(lower=0.0, upper=1.0)
    return frac * weight


def score_long(features: pd.DataFrame) -> pd.Series:
    rrs = features["rolling_rrs_d1"]
    w2 = _linear_score(rrs, 1.0, 3.0, W2_D1_RRS)

    ha = features["ha_cont_d1"].clip(lower=0)
    w3 = (ha.clip(upper=4) / 4.0) * W3_CHART_QUALITY

    # divergence bonus: stock up while SPY flat/down (or far stronger than
    # proportional) scores full; ordinary proportional strength scores less.
    diverges = (features["day_pc"] > 0) & (features["bench_day_pc"] <= 0)
    w4 = pd.Series(0.0, index=features.index)
    w4[diverges] = W4_DIVERGENCE
    proportional = ~diverges & (rrs > 1.0)
    w4[proportional] = _linear_score(rrs, 1.0, 3.0, W4_DIVERGENCE * 0.53)[proportional]

    w5 = _linear_score(features["volume_ratio_d1"], 1.0, 2.0, W5_VOLUME)

    hr = features["headroom_long"]
    w6 = pd.Series(W6_HEADROOM, index=features.index)  # NaN headroom = infinite = full score
    valid_hr = hr.notna()
    w6[valid_hr] = _linear_score(hr, 1.0, 2.0, W6_HEADROOM)[valid_hr]

    std = features["rrs_d1_std"]
    w7 = pd.Series(0.0, index=features.index)
    has_std = std.notna()
    w7[has_std] = ((1.0 - (std / 2.0).clip(0.0, 1.0)) * W7_CONSISTENCY)[has_std]

    total = w2 + w3 + w4 + w5 + w6 + w7
    missing = rrs.isna() | features["ha_cont_d1"].isna() | features["volume_ratio_d1"].isna()
    total[missing] = np.nan
    return total.rename("score_long")


def score_short(features: pd.DataFrame) -> pd.Series:
    rrs = features["rolling_rrs_d1"]
    w2 = _linear_score(-rrs, 1.0, 3.0, W2_D1_RRS)

    ha = (-features["ha_cont_d1"]).clip(lower=0)
    w3 = (ha.clip(upper=4) / 4.0) * W3_CHART_QUALITY

    diverges = (features["day_pc"] < 0) & (features["bench_day_pc"] >= 0)
    w4 = pd.Series(0.0, index=features.index)
    w4[diverges] = W4_DIVERGENCE
    proportional = ~diverges & (rrs < -1.0)
    w4[proportional] = _linear_score(-rrs, 1.0, 3.0, W4_DIVERGENCE * 0.53)[proportional]

    w5 = _linear_score(features["volume_ratio_d1"], 1.0, 2.0, W5_VOLUME)

    hr = features["headroom_short"]
    w6 = pd.Series(W6_HEADROOM, index=features.index)
    valid_hr = hr.notna()
    w6[valid_hr] = _linear_score(hr, 1.0, 2.0, W6_HEADROOM)[valid_hr]

    std = features["rrs_d1_std"]
    w7 = pd.Series(0.0, index=features.index)
    has_std = std.notna()
    w7[has_std] = ((1.0 - (std / 2.0).clip(0.0, 1.0)) * W7_CONSISTENCY)[has_std]

    total = w2 + w3 + w4 + w5 + w6 + w7
    missing = rrs.isna() | features["ha_cont_d1"].isna() | features["volume_ratio_d1"].isna()
    total[missing] = np.nan
    return total.rename("score_short")


W1_M5_RRS = 25.0
W2_D1_RRS_M5 = 20.0
W3_CHART_QUALITY_M5 = 15.0
W4_DIVERGENCE_M5 = 15.0
W5_VOLUME_M5 = 10.0
W6_HEADROOM_M5 = 10.0
W7_CONSISTENCY_M5 = 5.0


def score_long_m5(features: pd.DataFrame) -> pd.Series:
    rrs_m5 = features["rolling_rrs_m5"]
    w1 = _linear_score(rrs_m5, 1.0, 3.0, W1_M5_RRS)

    rrs_d1 = features["rolling_rrs_d1"]
    w2 = _linear_score(rrs_d1, 0.5, 2.0, W2_D1_RRS_M5)

    ha = features["ha_cont_d1"].clip(lower=0)
    w3 = (ha.clip(upper=4) / 4.0) * W3_CHART_QUALITY_M5

    stock_pc = features["close"].diff(12)
    diverges = (stock_pc > 0) & (features["power_index_m5"] <= -1.0)
    w4 = pd.Series(0.0, index=features.index)
    w4[diverges] = W4_DIVERGENCE_M5
    proportional = ~diverges & (rrs_m5 > 1.0)
    w4[proportional] = _linear_score(rrs_m5, 1.0, 3.0, W4_DIVERGENCE_M5 * 0.53)[proportional]

    w5 = _linear_score(features["rvol_m5"], 1.0, 2.0, W5_VOLUME_M5)

    hr = features["headroom_long"]
    w6 = pd.Series(W6_HEADROOM_M5, index=features.index)
    valid_hr = hr.notna()
    w6[valid_hr] = _linear_score(hr, 1.0, 2.0, W6_HEADROOM_M5)[valid_hr]

    std = features["rrs_m5"].rolling(12).std()
    w7 = pd.Series(0.0, index=features.index)
    has_std = std.notna()
    w7[has_std] = ((1.0 - (std / 2.0).clip(0.0, 1.0)) * W7_CONSISTENCY_M5)[has_std]

    total = w1 + w2 + w3 + w4 + w5 + w6 + w7
    missing = rrs_m5.isna() | rrs_d1.isna() | features["ha_cont_d1"].isna() | features["rvol_m5"].isna()
    total[missing] = np.nan
    return total.rename("score_long_m5")


def score_short_m5(features: pd.DataFrame) -> pd.Series:
    rrs_m5 = features["rolling_rrs_m5"]
    w1 = _linear_score(-rrs_m5, 1.0, 3.0, W1_M5_RRS)

    rrs_d1 = features["rolling_rrs_d1"]
    w2 = _linear_score(-rrs_d1, 0.5, 2.0, W2_D1_RRS_M5)

    ha = (-features["ha_cont_d1"]).clip(lower=0)
    w3 = (ha.clip(upper=4) / 4.0) * W3_CHART_QUALITY_M5

    stock_pc = features["close"].diff(12)
    diverges = (stock_pc < 0) & (features["power_index_m5"] >= 1.0)
    w4 = pd.Series(0.0, index=features.index)
    w4[diverges] = W4_DIVERGENCE_M5
    proportional = ~diverges & (rrs_m5 < -1.0)
    w4[proportional] = _linear_score(-rrs_m5, 1.0, 3.0, W4_DIVERGENCE_M5 * 0.53)[proportional]

    w5 = _linear_score(features["rvol_m5"], 1.0, 2.0, W5_VOLUME_M5)

    hr = features["headroom_short"]
    w6 = pd.Series(W6_HEADROOM_M5, index=features.index)
    valid_hr = hr.notna()
    w6[valid_hr] = _linear_score(hr, 1.0, 2.0, W6_HEADROOM_M5)[valid_hr]

    std = features["rrs_m5"].rolling(12).std()
    w7 = pd.Series(0.0, index=features.index)
    has_std = std.notna()
    w7[has_std] = ((1.0 - (std / 2.0).clip(0.0, 1.0)) * W7_CONSISTENCY_M5)[has_std]

    total = w1 + w2 + w3 + w4 + w5 + w6 + w7
    missing = rrs_m5.isna() | rrs_d1.isna() | features["ha_cont_d1"].isna() | features["rvol_m5"].isna()
    total[missing] = np.nan
    return total.rename("score_short_m5")
