"""Pure form helpers: field specs + coercion (no streamlit import)."""
import dataclasses

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.ui.form import (
    ADVANCED_FIELDS,
    DIP_HOLD_MODES,
    KNOWN_GATES,
    build_config,
    coerce,
    field_specs,
)


def test_form_module_does_not_import_streamlit():
    import rs_spy.ui.form as form
    assert "streamlit" not in getattr(form, "__dict__", {})
    assert not hasattr(form, "st")


def test_field_specs_cover_every_config_field_once():
    cfg = BacktestConfigM5()
    specs = field_specs(cfg)
    assert [s["name"] for s in specs] == [f.name for f in dataclasses.fields(cfg)]
    by_name = {s["name"]: s for s in specs}
    assert by_name["shorts_enabled"]["kind"] == "bool"
    assert by_name["rrs_m5_window"]["kind"] == "int"
    assert by_name["stop_atr_mult"]["kind"] == "float"
    assert by_name["dip_hold_mode"] == {
        "name": "dip_hold_mode", "kind": "choice", "value": cfg.dip_hold_mode,
        "choices": DIP_HOLD_MODES, "advanced": False,
    }
    assert by_name["disabled_gates"]["kind"] == "gates"
    assert by_name["extra_symbols"]["kind"] == "symbols"
    assert by_name["extra_symbols"]["advanced"] is True
    assert by_name["universe_file"]["kind"] == "str"


def test_coerce_symbols_and_gates():
    assert coerce("symbols", " AAPL, HOOD ,") == ("AAPL", "HOOD")
    assert coerce("symbols", "") == ()
    assert coerce("gates", ["bias", "sma"]) == frozenset({"bias", "sma"})
    assert coerce("int", "12") == 12
    assert coerce("float", 1.5) == 1.5
    assert coerce("bool", True) is True


def test_build_config_round_trips_defaults_and_applies_changes():
    cfg = BacktestConfigM5()
    specs = field_specs(cfg)
    values = {s["name"]: s["value"] for s in specs}
    assert build_config(cfg, values) == cfg          # untouched form == defaults
    values["rrs_m5_window"] = 24
    values["extra_symbols"] = "HOOD, SOFI"
    out = build_config(cfg, values)
    assert out.rrs_m5_window == 24
    assert out.extra_symbols == ("HOOD", "SOFI")


def test_known_gates_and_advanced_sets_are_the_spec_values():
    assert KNOWN_GATES == ["bias", "rrs", "rrs_m5", "vwap", "ha", "sma"]
    assert ADVANCED_FIELDS == {
        "extra_symbols", "universe_file", "trade_symbols_override", "disabled_gates",
    }
