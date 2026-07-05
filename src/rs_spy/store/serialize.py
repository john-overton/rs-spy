"""Convert backtest objects to/from Postgres-storable forms.

Three jobs, each with a gotcha the runs-store depends on:
  * config_to_jsonb  -- BacktestConfigM5.disabled_gates is a frozenset, which is
    not JSON-serializable; it must become a sorted list.
  * sanitize_metrics -- compute_metrics returns float('inf') for profit_factor /
    avg_win_loss_ratio on zero-loss runs, and numpy scalar types elsewhere.
    Postgres JSONB rejects Infinity/NaN and json.dumps rejects numpy scalars, so
    both are normalised (inf/nan -> None, numpy -> Python built-ins).
  * equity <-> bytes -- the equity curve is a ~97k-point tz-aware Series stored
    as a single parquet-compressed BYTEA blob (its only access pattern is
    "give me the whole curve for run X"), not a normalised per-point table.
"""
import dataclasses
import io
import math

import pandas as pd

from rs_spy.backtest.engine_m5 import BacktestConfigM5

_EQUITY_COL = "equity"


def config_to_jsonb(config: BacktestConfigM5) -> dict:
    """dataclass -> JSON-safe dict. frozenset disabled_gates -> sorted list."""
    d = dataclasses.asdict(config)
    d["disabled_gates"] = sorted(d["disabled_gates"])
    d["extra_symbols"] = list(d["extra_symbols"])
    d["trade_symbols_override"] = list(d["trade_symbols_override"])
    return d

def config_from_jsonb(data: dict) -> BacktestConfigM5:
    """Inverse of config_to_jsonb. Tolerates unknown/missing keys so a run
    stored under an older schema still round-trips (unknown keys dropped,
    missing keys fall back to the dataclass default)."""
    fields = {f.name for f in dataclasses.fields(BacktestConfigM5)}
    kwargs = {k: v for k, v in data.items() if k in fields}
    if "disabled_gates" in kwargs:
        kwargs["disabled_gates"] = frozenset(kwargs["disabled_gates"])
    if "extra_symbols" in kwargs:
        kwargs["extra_symbols"] = tuple(kwargs["extra_symbols"])
    if "trade_symbols_override" in kwargs:
        kwargs["trade_symbols_override"] = tuple(kwargs["trade_symbols_override"])
    return BacktestConfigM5(**kwargs)


def sanitize_metrics(metrics: dict) -> dict:
    """Make a metrics dict valid for JSONB: inf/nan -> None, numpy scalars ->
    Python built-ins. Recurses into nested dicts/lists (metrics_by_direction)."""
    return {k: _sanitize_value(v) for k, v in metrics.items()}


def _sanitize_value(v):
    if isinstance(v, dict):
        return {k: _sanitize_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanitize_value(x) for x in v]
    if isinstance(v, bool):
        return v
    # numpy scalars expose .item(); collapse them to Python int/float first.
    if hasattr(v, "item") and not isinstance(v, (int, float)):
        try:
            v = v.item()
        except (ValueError, TypeError):
            return v
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def equity_to_bytes(equity: pd.Series | None) -> tuple[bytes, int] | None:
    """Serialize an equity Series to (parquet_bytes, n_points). Returns None
    when there is no curve. The tz-aware UTC index is preserved by parquet."""
    if equity is None or len(equity) == 0:
        return None
    buf = io.BytesIO()
    equity.to_frame(name=_EQUITY_COL).to_parquet(buf, engine="pyarrow")
    return buf.getvalue(), int(len(equity))


def bytes_to_equity(data: bytes) -> pd.Series:
    """Inverse of equity_to_bytes -- reconstructs the Series with its index."""
    frame = pd.read_parquet(io.BytesIO(data), engine="pyarrow")
    return frame[_EQUITY_COL]
