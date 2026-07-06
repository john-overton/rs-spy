"""Config-form model: dataclass -> field specs -> widget values -> dataclass.

Pure module (NO streamlit import): pages render the specs with st widgets;
this module owns grouping, kinds, and coercion so it is unit-testable and the
form automatically tracks future BacktestConfigM5 fields (type-dispatched,
never a hand-maintained field list)."""
import dataclasses

from rs_spy.backtest.engine_m5 import BacktestConfigM5

KNOWN_GATES = ["bias", "rrs", "rrs_m5", "vwap", "ha", "sma"]
DIP_HOLD_MODES = ["strict", "d1_session", "grace"]
ADVANCED_FIELDS = {"extra_symbols", "universe_file", "trade_symbols_override", "disabled_gates"}


def field_specs(defaults: BacktestConfigM5) -> list[dict]:
    specs = []
    for f in dataclasses.fields(defaults):
        value = getattr(defaults, f.name)
        spec = {"name": f.name, "value": value, "advanced": f.name in ADVANCED_FIELDS}
        if f.name == "dip_hold_mode":
            spec |= {"kind": "choice", "choices": DIP_HOLD_MODES}
        elif f.name == "disabled_gates":
            spec |= {"kind": "gates", "value": sorted(value)}
        elif isinstance(value, bool):
            spec |= {"kind": "bool"}
        elif isinstance(value, int):
            # Latent trap: this branch fires for plain Python ints, and a
            # future BacktestConfigM5 float field whose default happens to be
            # written as a whole number (`= 5` instead of `= 5.0`) is a
            # Python int too -- it would be silently classified "int" here
            # and lose decimal coercion. Keep float defaults as `.0` literals.
            spec |= {"kind": "int"}
        elif isinstance(value, float):
            spec |= {"kind": "float"}
        elif isinstance(value, tuple):
            spec |= {"kind": "symbols", "value": ", ".join(value)}
        else:
            spec |= {"kind": "str"}
        specs.append(spec)
    return specs


def coerce(kind: str, raw):
    if kind == "symbols":
        if isinstance(raw, tuple):
            return raw
        return tuple(s.strip() for s in str(raw).split(",") if s.strip())
    if kind == "gates":
        return frozenset(raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return bool(raw)
    return raw  # str / choice


def build_config(defaults: BacktestConfigM5, values: dict) -> BacktestConfigM5:
    kinds = {s["name"]: s["kind"] for s in field_specs(defaults)}
    coerced = {name: coerce(kinds[name], raw) for name, raw in values.items()}
    return dataclasses.replace(defaults, **coerced)
