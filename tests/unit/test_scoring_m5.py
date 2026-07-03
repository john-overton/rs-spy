import pandas as pd

from rs_spy.selection.scoring import score_long_m5, score_short_m5

_N = 10


def _features_m5(**overrides):
    base = {
        "rolling_rrs_m5": [2.0] * _N,
        "rolling_rrs_d1": [1.0] * _N,
        "ha_cont_d1": [3] * _N,
        "power_index_m5": [-1.5] * _N,
        "close": [50.0] * _N,
        "rvol_m5": [1.5] * _N,
        "headroom_long": [1.5] * _N,
        "headroom_short": [1.5] * _N,
        "rrs_m5": [2.0] * _N,
    }
    base.update(overrides)
    return pd.DataFrame(base, index=pd.RangeIndex(_N))


def test_score_long_m5_is_higher_for_stronger_rrs():
    weak = score_long_m5(_features_m5(rolling_rrs_m5=[1.1] * _N))
    strong = score_long_m5(_features_m5(rolling_rrs_m5=[2.9] * _N))
    assert strong.iloc[-1] > weak.iloc[-1]


def test_score_long_m5_within_0_100():
    out = score_long_m5(_features_m5())
    assert (out.dropna() >= 0).all()
    assert (out.dropna() <= 100).all()


def test_score_short_m5_mirrors_long_for_symmetric_inputs():
    long_feat = _features_m5(rolling_rrs_m5=[2.0] * _N, power_index_m5=[-1.5] * _N)
    short_feat = _features_m5(rolling_rrs_m5=[-2.0] * _N, power_index_m5=[1.5] * _N, rolling_rrs_d1=[-1.0] * _N, ha_cont_d1=[-3] * _N)
    long_score = score_long_m5(long_feat).iloc[-1]
    short_score = score_short_m5(short_feat).iloc[-1]
    assert abs(long_score - short_score) < 1e-6
