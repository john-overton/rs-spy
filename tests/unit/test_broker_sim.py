import pytest

from rs_spy.backtest.broker_sim import apply_slippage, entry_limit_price, try_fill_entry


def test_entry_limit_price_long_and_short():
    assert entry_limit_price(last_price=100.0, atr_m5=2.0, direction="LONG") == pytest.approx(100.2)
    assert entry_limit_price(last_price=100.0, atr_m5=2.0, direction="SHORT") == pytest.approx(99.8)


def test_try_fill_entry_long_fills_at_limit_when_open_gaps_through_unfavorably():
    # bar opens above the limit (worse for a buyer) but trades back down through it
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=100.5, bar_high=100.6, bar_low=100.1)
    assert fill == pytest.approx(100.2)


def test_try_fill_entry_long_fills_at_open_when_open_gaps_through_favorably():
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=99.9, bar_high=100.0, bar_low=99.8)
    assert fill == pytest.approx(99.9)


def test_try_fill_entry_long_no_fill_when_bar_never_reaches_limit():
    fill = try_fill_entry("LONG", limit_price=100.2, bar_open=100.5, bar_high=100.8, bar_low=100.3)
    assert fill is None


def test_try_fill_entry_short_mirrors_long():
    fill = try_fill_entry("SHORT", limit_price=99.8, bar_open=99.5, bar_high=99.9, bar_low=99.4)
    assert fill == pytest.approx(99.8)
    fill2 = try_fill_entry("SHORT", limit_price=99.8, bar_open=100.1, bar_high=100.2, bar_low=100.0)
    assert fill2 == pytest.approx(100.1)


def test_try_fill_entry_short_phantom_fill_regression():
    # Regression: bar high (99.5) never reaches limit (99.8), so should NOT fill
    fill = try_fill_entry("SHORT", limit_price=99.8, bar_open=99.0, bar_high=99.5, bar_low=98.5)
    assert fill is None


def test_try_fill_entry_short_missed_fill_regression():
    # Regression: whole bar trades at/above limit (high=100.3 >> 99.8), should fill at open
    fill = try_fill_entry("SHORT", limit_price=99.8, bar_open=100.0, bar_high=100.3, bar_low=99.9)
    assert fill == pytest.approx(100.0)


def test_try_fill_entry_short_fills_at_open_when_open_gaps_through_favorably():
    # Mirror of LONG case: bar opens above (favorable for a short seller)
    fill = try_fill_entry("SHORT", limit_price=99.8, bar_open=100.1, bar_high=100.2, bar_low=99.9)
    assert fill == pytest.approx(100.1)


def test_apply_slippage_long_entry_and_exit():
    entry = apply_slippage(100.0, direction="LONG", is_entry=True, bps=2.0)
    exit_ = apply_slippage(100.0, direction="LONG", is_entry=False, bps=2.0)
    assert entry == pytest.approx(100.02)
    assert exit_ == pytest.approx(99.98)


def test_apply_slippage_short_entry_and_exit():
    entry = apply_slippage(100.0, direction="SHORT", is_entry=True, bps=2.0)
    exit_ = apply_slippage(100.0, direction="SHORT", is_entry=False, bps=2.0)
    assert entry == pytest.approx(99.98)
    assert exit_ == pytest.approx(100.02)
