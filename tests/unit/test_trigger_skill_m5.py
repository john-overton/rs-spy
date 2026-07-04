import pandas as pd
import pytest

from rs_spy.backtest.studies.trigger_skill_m5 import trigger_skill_table
from rs_spy.bias.buckets import LONG_TRIGGER, NO_TRIGGER, SHORT_TRIGGER


def _calendar(n):
    return pd.date_range("2026-03-02 14:30", periods=n, freq="5min", tz="UTC")


def test_trigger_skill_table_hand_computed_long_rows():
    idx = _calendar(10)
    close = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109], index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    trigger.iloc[1] = LONG_TRIGGER
    trigger.iloc[4] = LONG_TRIGGER
    trigger.iloc[9] = LONG_TRIGGER  # no bar 2 ahead -> forward return NaN -> excluded from n

    out = trigger_skill_table(trigger, close, horizons=(2,), flat_threshold_pct=0.001)

    long_row = out[(out["signal"] == LONG_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert long_row["n"] == 2
    assert long_row["pct_up"] == 1.0
    assert long_row["pct_down"] == 0.0
    expected_mean = ((103 / 101 - 1) + (106 / 104 - 1)) / 2
    assert long_row["mean_fwd_return"] == pytest.approx(expected_mean)

    all_row = out[(out["signal"] == "ALL") & (out["horizon_bars"] == 2)].iloc[0]
    assert all_row["n"] == 8  # bars 0..7 have a bar 2 ahead
    assert all_row["pct_up"] == 1.0  # monotonically rising fixture

    short_row = out[(out["signal"] == SHORT_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert short_row["n"] == 0
    assert short_row["pct_up"] is None


def test_trigger_skill_table_classifies_down_and_flat_moves():
    idx = _calendar(8)
    # bars 0-3 fall hard, bars 4-7 are pinned flat
    close = pd.Series([100.0, 98.0, 96.0, 94.0, 92.0, 92.0, 92.0, 92.0], index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    trigger.iloc[0] = SHORT_TRIGGER  # fwd(2) = 96/100 - 1 = -4% -> DOWN
    trigger.iloc[4] = SHORT_TRIGGER  # fwd(2) = 92/92 - 1 = 0% -> FLAT

    out = trigger_skill_table(trigger, close, horizons=(2,), flat_threshold_pct=0.001)
    short_row = out[(out["signal"] == SHORT_TRIGGER) & (out["horizon_bars"] == 2)].iloc[0]
    assert short_row["n"] == 2
    assert short_row["pct_down"] == 0.5
    assert short_row["pct_flat"] == 0.5
    assert short_row["pct_up"] == 0.0


def test_trigger_skill_table_emits_one_row_per_signal_per_horizon():
    idx = _calendar(30)
    close = pd.Series(100.0, index=idx)
    trigger = pd.Series(NO_TRIGGER, index=idx, dtype=object)
    out = trigger_skill_table(trigger, close, horizons=(2, 6), flat_threshold_pct=0.001)
    assert len(out) == 6  # 2 horizons x (ALL, LONG_TRIGGER, SHORT_TRIGGER)
    assert set(out["signal"]) == {"ALL", LONG_TRIGGER, SHORT_TRIGGER}
    assert set(out["horizon_bars"]) == {2, 6}
