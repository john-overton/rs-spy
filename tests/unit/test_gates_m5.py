import pandas as pd

from rs_spy.indicators.sma_stack import ABOVE_ALL
from rs_spy.selection import gates

_N = 20


def _features_m5(rrs_m5=2.0, above_vwap=True, one_candle_wonder=False, gap_pct=0.02, rrs_qqq=None):
    idx = pd.RangeIndex(_N)
    close = [50.0] * _N
    vwap = [49.0 if above_vwap else 51.0] * _N
    data = {
        "close": close,
        "vwap_m5": vwap,
        "rolling_rrs_m5": [rrs_m5] * _N,
        "one_candle_wonder": [one_candle_wonder] * _N,
        "gap_pct": [gap_pct] * _N,
        "rolling_rrs_d1": [2.0] * _N,
        "rrs_d1": [2.0] * _N,
        "ha_cont_d1": [3] * _N,
        "sma_stack": [ABOVE_ALL] * _N,
        "headroom_long": [None] * _N,
        "volume_ratio_d1": [10.0] * _N,
    }
    if rrs_qqq is not None:
        data["rolling_rrs_m5_qqq"] = [rrs_qqq] * _N
    return pd.DataFrame(data, index=idx)


def _df():
    return pd.DataFrame({"close": [50.0] * _N, "volume": [2_000_000.0] * _N})


def test_gate_vwap_long_requires_close_above_vwap():
    feat_above = _features_m5(above_vwap=True)
    feat_below = _features_m5(above_vwap=False)
    assert gates.gate_vwap_long(feat_above).iloc[-1]
    assert not gates.gate_vwap_long(feat_below).iloc[-1]


def test_gates_pass_long_m5_requires_all_gates():
    df = _df()
    assert gates.gates_pass_long_m5(df, _features_m5(), min_adv_shares=1.0).iloc[-1]
    assert not gates.gates_pass_long_m5(df, _features_m5(above_vwap=False), min_adv_shares=1.0).iloc[-1]
    assert not gates.gates_pass_long_m5(df, _features_m5(rrs_m5=-2.0), min_adv_shares=1.0).iloc[-1]


def test_one_candle_wonder_excludes_even_if_otherwise_qualified():
    df = _df()
    feat = _features_m5(one_candle_wonder=True)
    assert not gates.gates_pass_long_m5(df, feat, min_adv_shares=1.0).iloc[-1]


def test_large_gap_excludes():
    df = _df()
    feat = _features_m5(gap_pct=0.35)
    assert not gates.gates_pass_long_m5(df, feat, min_adv_shares=1.0).iloc[-1]


def test_qqq_crosscheck_only_enforced_when_requested():
    df = _df()
    feat = _features_m5(rrs_qqq=-2.0)  # fails vs QQQ
    assert gates.gates_pass_long_m5(df, feat, min_adv_shares=1.0).iloc[-1]  # off by default
    assert not gates.gates_pass_long_m5(
        df, feat, min_adv_shares=1.0, use_qqq_crosscheck=True
    ).iloc[-1]
