import json

import numpy as np
import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.store.serialize import (
    bytes_to_equity,
    config_from_jsonb,
    config_to_jsonb,
    equity_to_bytes,
    sanitize_metrics,
)


def test_config_to_jsonb_is_json_serializable_and_sorts_disabled_gates():
    config = BacktestConfigM5(disabled_gates=frozenset({"rrs", "bias", "ha"}), shorts_enabled=True)
    d = config_to_jsonb(config)
    # frozenset -> sorted list (deterministic, JSON-safe)
    assert d["disabled_gates"] == ["bias", "ha", "rrs"]
    assert d["shorts_enabled"] is True
    # the whole thing must survive json.dumps (what psycopg's Jsonb does)
    json.dumps(d)


def test_config_round_trips_through_jsonb():
    config = BacktestConfigM5(
        disabled_gates=frozenset({"bias"}), rrs_m5_window=24, shorts_enabled=True, stop_atr_mult=1.5
    )
    restored = config_from_jsonb(config_to_jsonb(config))
    assert restored == config
    assert restored.disabled_gates == frozenset({"bias"})


def test_config_from_jsonb_tolerates_unknown_and_missing_keys():
    d = config_to_jsonb(BacktestConfigM5())
    d["a_removed_field"] = 123          # unknown -> dropped
    del d["stop_atr_mult"]              # missing -> default
    restored = config_from_jsonb(d)
    assert restored.stop_atr_mult == BacktestConfigM5().stop_atr_mult


def test_sanitize_metrics_maps_inf_and_nan_to_none():
    metrics = {
        "n_trades": 3,
        "profit_factor": float("inf"),
        "avg_win_loss_ratio": float("nan"),
        "win_rate": 0.66,
        "total_pnl": -123.4,
    }
    out = sanitize_metrics(metrics)
    assert out["profit_factor"] is None
    assert out["avg_win_loss_ratio"] is None
    assert out["win_rate"] == 0.66
    json.dumps(out)  # must be JSONB-valid


def test_sanitize_metrics_coerces_numpy_scalars():
    metrics = {"total_pnl": np.float64(42.5), "n_trades": np.int64(7)}
    out = sanitize_metrics(metrics)
    assert isinstance(out["total_pnl"], float) and out["total_pnl"] == 42.5
    assert isinstance(out["n_trades"], int) and out["n_trades"] == 7
    json.dumps(out)


def test_sanitize_metrics_recurses_into_nested_dicts():
    metrics = {"by_dir": {"LONG": {"profit_factor": float("inf"), "n_trades": np.int64(2)}}}
    out = sanitize_metrics(metrics)
    assert out["by_dir"]["LONG"]["profit_factor"] is None
    assert out["by_dir"]["LONG"]["n_trades"] == 2


def test_equity_bytes_round_trip_preserves_tz_aware_index():
    idx = pd.date_range("2026-02-02 09:30", periods=100, freq="5min", tz="UTC")
    equity = pd.Series(np.linspace(100_000, 101_000, 100), index=idx, name="equity")
    blob, n = equity_to_bytes(equity)
    assert n == 100
    restored = bytes_to_equity(blob)
    # check_freq=False: real backtest curves have an irregular RTH index with no
    # freq; parquet doesn't round-trip the freq attr this fixture happens to set.
    pd.testing.assert_series_equal(restored, equity, check_freq=False)
    assert str(restored.index.tz) == "UTC"


def test_equity_to_bytes_none_and_empty_return_none():
    assert equity_to_bytes(None) is None
    assert equity_to_bytes(pd.Series([], dtype=float)) is None
