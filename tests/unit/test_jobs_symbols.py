"""jobs.runner._trade_symbols: curated + extra_symbols merge."""
import pytest

from rs_spy.backtest.engine_m5 import BacktestConfigM5
from rs_spy.jobs.runner import _trade_symbols
from rs_spy.universe import BenchmarkSpec, SymbolSpec, Universe

UNIVERSE = Universe(
    benchmarks=[BenchmarkSpec(symbol="SPY", role="primary"),
                BenchmarkSpec(symbol="QQQ", role="secondary")],
    universe=[SymbolSpec(symbol="AAPL", sector="Technology"),
              SymbolSpec(symbol="JPM", sector="Financials")],
)


@pytest.fixture
def universe():
    """Universe fixture for testing _trade_symbols."""
    return Universe(
        benchmarks=[BenchmarkSpec(symbol="SPY", role="primary"),
                    BenchmarkSpec(symbol="QQQ", role="secondary")],
        universe=[SymbolSpec(symbol="AAPL", sector="Technology"),
                  SymbolSpec(symbol="MSFT", sector="Technology"),
                  SymbolSpec(symbol="JPM", sector="Financials")],
    )


def test_default_config_reproduces_the_curated_universe_exactly():
    assert _trade_symbols(UNIVERSE, BacktestConfigM5()) == ["AAPL", "JPM"]


def test_extra_symbols_are_appended_and_dupes_of_curated_or_benchmarks_dropped():
    cfg = BacktestConfigM5(extra_symbols=("HOOD", "AAPL", "SPY", "SOFI"))
    assert _trade_symbols(UNIVERSE, cfg) == ["AAPL", "JPM", "HOOD", "SOFI"]


def test_override_replaces_the_trade_list(universe):
    cfg = BacktestConfigM5(trade_symbols_override=("AAPL", "MSFT"))
    assert _trade_symbols(universe, cfg) == ["AAPL", "MSFT"]


def test_override_may_draw_from_extra_symbols(universe):
    cfg = BacktestConfigM5(
        extra_symbols=("HOOD",), trade_symbols_override=("AAPL", "HOOD")
    )
    assert _trade_symbols(universe, cfg) == ["AAPL", "HOOD"]


def test_override_with_unknown_symbol_raises(universe):
    cfg = BacktestConfigM5(trade_symbols_override=("AAPL", "ZZZQ"))
    with pytest.raises(ValueError, match="ZZZQ"):
        _trade_symbols(universe, cfg)


def test_empty_override_keeps_existing_behavior(universe):
    assert _trade_symbols(universe, BacktestConfigM5()) == universe.trade_symbols
