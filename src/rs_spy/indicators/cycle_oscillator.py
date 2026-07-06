"""1OP-style two-line cycle oscillator on M5 bars (M11 Phase 1).

Two input families (spec 2026-07-05-cycle-oscillator-design.md):
  * "close"    -- PPO: 100 * (EMA(close, fast) - EMA(close, slow)) / EMA(close, slow).
  * "vwap_dev" -- fast EMA of the percentage deviation from session VWAP
                  (price+volume composite; `slow` is unused by this formula and
                  kept on the spec only for uniform grid bookkeeping).
Both: signal_line = EMA(fast_line, signal); histogram = fast_line - signal_line.

Causal by construction (adjust=False EWMs, no shifts backward). The oscillator's
EMA state deliberately carries across sessions (only the VWAP input resets
daily) -- the conventional way MACD-family indicators are run intraday,
documented rather than silently chosen.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from rs_spy.indicators.vwap import vwap

STATES = ("BULL_RUN", "BULL_EARLY", "BEAR_EARLY", "BEAR_RUN")
INPUT_MODES = ("close", "vwap_dev")


@dataclass(frozen=True)
class OscSpec:
    input_mode: str
    fast: int
    slow: int
    signal: int

    @property
    def name(self) -> str:
        return f"{self.input_mode}-{self.fast}-{self.slow}-{self.signal}"


def compute_oscillator(m5: pd.DataFrame, spec: OscSpec) -> pd.DataFrame:
    """fast_line / signal_line / histogram on m5's index (RTH M5 bars)."""
    if spec.input_mode == "close":
        ema_fast = m5["close"].ewm(span=spec.fast, adjust=False).mean()
        ema_slow = m5["close"].ewm(span=spec.slow, adjust=False).mean()
        fast_line = 100.0 * (ema_fast - ema_slow) / ema_slow
    elif spec.input_mode == "vwap_dev":
        session_vwap = vwap(m5)
        dev = 100.0 * (m5["close"] - session_vwap) / session_vwap
        fast_line = dev.ewm(span=spec.fast, adjust=False).mean()
    else:
        raise ValueError(f"unknown input_mode: {spec.input_mode!r}")

    signal_line = fast_line.ewm(span=spec.signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "fast_line": fast_line,
            "signal_line": signal_line,
            "histogram": fast_line - signal_line,
        }
    )


def oscillator_states(osc: pd.DataFrame) -> pd.Series:
    """The 4-state read: (fast vs signal) x (fast vs zero). NaN-preserving."""
    fast, signal = osc["fast_line"], osc["signal_line"]
    above_signal = fast > signal
    above_zero = fast > 0
    states = np.select(
        [
            above_signal & above_zero,
            above_signal & ~above_zero,
            ~above_signal & above_zero,
        ],
        ["BULL_RUN", "BULL_EARLY", "BEAR_EARLY"],
        default="BEAR_RUN",
    )
    out = pd.Series(states, index=osc.index, dtype=object)
    out[fast.isna() | signal.isna()] = np.nan
    return out


def oscillator_crosses(osc: pd.DataFrame) -> pd.DataFrame:
    """True only on the crossing bar."""
    fast, signal = osc["fast_line"], osc["signal_line"]
    above = fast > signal
    above_prev = above.shift(1).fillna(False).astype(bool)
    pos = fast > 0
    pos_prev = pos.shift(1).fillna(False).astype(bool)
    return pd.DataFrame(
        {
            "bull_cross": above & ~above_prev,
            "bear_cross": ~above & above_prev,
            "zero_up": pos & ~pos_prev,
            "zero_down": ~pos & pos_prev,
        }
    )
