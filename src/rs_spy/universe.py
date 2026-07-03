from pathlib import Path

import yaml
from pydantic import BaseModel


class BenchmarkSpec(BaseModel):
    symbol: str
    role: str


class SymbolSpec(BaseModel):
    symbol: str
    sector: str


class Universe(BaseModel):
    benchmarks: list[BenchmarkSpec]
    universe: list[SymbolSpec]

    @property
    def benchmark_symbols(self) -> list[str]:
        return [b.symbol for b in self.benchmarks]

    @property
    def primary_benchmark(self) -> str:
        for b in self.benchmarks:
            if b.role == "primary":
                return b.symbol
        return self.benchmark_symbols[0]

    @property
    def secondary_benchmark(self) -> str:
        for b in self.benchmarks:
            if b.role == "secondary":
                return b.symbol
        return self.benchmark_symbols[-1]

    @property
    def trade_symbols(self) -> list[str]:
        return [s.symbol for s in self.universe]

    @property
    def all_symbols(self) -> list[str]:
        """Benchmarks + trade universe, de-duplicated, order-preserving."""
        seen: set[str] = set()
        out: list[str] = []
        for sym in [*self.benchmark_symbols, *self.trade_symbols]:
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
        return out

    def sector_of(self, symbol: str) -> str | None:
        for s in self.universe:
            if s.symbol == symbol:
                return s.sector
        return None


def load_universe(path: Path) -> Universe:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Universe.model_validate(raw)


def load_earnings_blackout(path: Path) -> dict[str, set]:
    import pandas as pd

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    out: dict[str, set] = {}
    for sym, entry in (raw.get("symbols") or {}).items():
        dates = entry.get("earnings_blackout") or []
        out[sym] = {pd.Timestamp(d).normalize() for d in dates}
    return out
