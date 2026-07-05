"""jobs.runner._trade_symbols: curated + extra_symbols merge."""
from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.jobs.runner import _trade_symbols
from rs_spy.universe import BenchmarkSpec, SymbolSpec, Universe

UNIVERSE = Universe(
    benchmarks=[BenchmarkSpec(symbol="SPY", role="primary"),
                BenchmarkSpec(symbol="QQQ", role="secondary")],
    universe=[SymbolSpec(symbol="AAPL", sector="Technology"),
              SymbolSpec(symbol="JPM", sector="Financials")],
)


def test_default_config_reproduces_the_curated_universe_exactly():
    assert _trade_symbols(UNIVERSE, BacktestConfigM5()) == ["AAPL", "JPM"]


def test_extra_symbols_are_appended_and_dupes_of_curated_or_benchmarks_dropped():
    cfg = BacktestConfigM5(extra_symbols=("HOOD", "AAPL", "SPY", "SOFI"))
    assert _trade_symbols(UNIVERSE, cfg) == ["AAPL", "JPM", "HOOD", "SOFI"]
