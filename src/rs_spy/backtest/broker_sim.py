"""Order fill simulation. algo-spec/07 §5, 08 §1.

Entries: marketable-limit orders (07 §5) -- long limit = last + 0.1*ATR_M5, short
limit = last - 0.1*ATR_M5 -- filled on the bar AFTER the one that produced the
entry signal (08 §1's "fills at next-bar prices" no-lookahead rule; the caller in
backtest/engine_m5.py is responsible for only calling try_fill_entry on bars after
the signal bar), at the better of the limit price or that bar's own open, provided
the bar's range actually reaches the limit; unfilled after
DEFAULT_UNFILLED_CANCEL_BARS bars, the caller cancels ("never chase -- the state
machine will re-arm").

Exits: market orders, filled at the same bar's close that produced the exit signal
-- matching backtest/engine.py's existing D1 convention (an exit decided from bar
i's own closed data fills at bar i's own close, not a stricter next-bar model);
07 §5's "getting out matters more than the fill" supports treating same-bar-close
as an acceptable approximation. This module only provides the slippage adjustment
for that fill price -- the fill price itself (bar close, or the stop level on a
hard-stop exit) is computed by the caller, same as backtest/engine.py's D1 pattern.
"""

ENTRY_LIMIT_ATR_MULT = 0.1
DEFAULT_UNFILLED_CANCEL_BARS = 2
SLIPPAGE_BPS = 2.0


def entry_limit_price(last_price: float, atr_m5: float, direction: str) -> float:
    offset = ENTRY_LIMIT_ATR_MULT * atr_m5
    return last_price + offset if direction == "LONG" else last_price - offset


def try_fill_entry(direction: str, limit_price: float, bar_open: float, bar_high: float, bar_low: float) -> float | None:
    """Returns the fill price if this bar's range reaches the limit, else None
    (order stays pending). Fill price is the better of the limit and the bar's
    open -- never worse than the limit itself."""
    if direction == "LONG":
        if bar_low <= limit_price:
            return min(limit_price, bar_open) if bar_open <= limit_price else limit_price
        return None
    if bar_high >= limit_price:
        return max(limit_price, bar_open) if bar_open >= limit_price else limit_price
    return None


def apply_slippage(price: float, direction: str, is_entry: bool, bps: float = SLIPPAGE_BPS) -> float:
    """Slippage always moves the fill against the trader. A LONG entry or a
    SHORT exit is a buy (fills higher); a SHORT entry or a LONG exit is a sell
    (fills lower)."""
    is_buy = (direction == "LONG") == is_entry
    factor = 1.0 + bps / 10_000.0 if is_buy else 1.0 - bps / 10_000.0
    return price * factor
