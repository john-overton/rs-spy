"""M10 config fields: universe_file + trade_symbols_override are inert in the
engine, round-trip through JSONB, and drive the runner's symbol selection."""
import dataclasses

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.store.serialize import config_from_jsonb, config_to_jsonb


def test_new_fields_are_the_last_two_with_safe_defaults():
    names = [f.name for f in dataclasses.fields(BacktestConfigM5)]
    assert names[-2:] == ["universe_file", "trade_symbols_override"]
    cfg = BacktestConfigM5()
    assert cfg.universe_file == "universe.yaml"
    assert cfg.trade_symbols_override == ()


def test_jsonb_round_trip_preserves_override_tuple():
    cfg = BacktestConfigM5(
        universe_file="universe_500.yaml",
        trade_symbols_override=("AAPL", "HOOD"),
    )
    data = config_to_jsonb(cfg)
    assert data["trade_symbols_override"] == ["AAPL", "HOOD"]  # JSON-safe list
    back = config_from_jsonb(data)
    assert back == cfg  # tuple restored -> dataclass equality holds


def test_from_jsonb_tolerates_configs_stored_before_these_fields_existed():
    old = config_to_jsonb(BacktestConfigM5())
    del old["universe_file"], old["trade_symbols_override"]
    back = config_from_jsonb(old)
    assert back.universe_file == "universe.yaml"
    assert back.trade_symbols_override == ()
