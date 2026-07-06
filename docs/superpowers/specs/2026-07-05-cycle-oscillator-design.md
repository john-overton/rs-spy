# Spec: 1OP-style cycle oscillator — measure first, gate second (M11 Phase 1)

**Status**: approved design (brainstormed 2026-07-05, evening). Input to `writing-plans`.
**Branch**: `m11-cycle-oscillator` (user-directed; main stays untouched until the gate decision).
**Depends on**: the M7/M7.5 study harness patterns (`backtest/studies/`), the M5 bias engine
(`bias/engine.py`, the incumbent), the trendline trigger (`bias/trigger.py`), warehouse SPY M5 data.

## Purpose

M7 measured the current 8-component bias engine at **zero directional skill** above base rate
for SPY forward returns, while M7.5 measured real skill in the LONG trendline trigger. Trades
only enter when a skilled trigger lands inside an unskilled bias window — few trades, and the
ones that pass are effectively unfiltered w.r.t. market cycle (M10: PF 0.86 at 500 symbols).
The OneOption thesis (user-supplied, 2026-07-05): a 1OP-style **market cycle oscillator** (two
lines crossing around zero, MACD family, on SPY) is the missing "which way and when" layer;
RRS then supplies "what". This milestone builds candidate oscillators and **measures** whether
any has out-of-sample skill — nothing gets wired into the engine until it passes a
pre-committed holdout gate.

Guiding note from the user: *1OP is probably simple* ("some simple valve based on pure
observations and intuition"). Prefer the simplest candidates; a modest grid; no exotic
composites. And: *keep it modular* — the oscillator API should be pleasant to experiment with
by hand before/beyond the formal study.

## Decisions (brainstorming outcomes)

- **Measure first, gate second** (hard gate, no exceptions): train-period winner → single
  holdout evaluation → pass = wire in Phase 2 (its own plan); fail = publish the null, keep
  the current engine, stop. If the holdout is ever failed, the 2025–2026 window is burned and
  any second iteration needs walk-forward validation, not a re-test.
- **Candidate families**: (1) MACD/PPO on close; (2) price+volume composite via session-VWAP
  deviation. (Ehlers cycle family and recombined-bias-components were considered and excluded
  for v1.)
- **State vocabulary is the deliverable**: the oscillator is read as a **group of 4 states**
  (fast vs signal line × above/below zero), plus cross events. "We will see what it looks like
  in practice" — the study reports per-state behavior, not just a scalar score.
- **Divergence reads** (1OP's ~20% disagreement-as-signal): explicitly v2.

## The indicator (`src/rs_spy/indicators/cycle_oscillator.py`)

Pure module, no I/O, hermetic-tested against hand-computed fixtures. Modular API:

```python
@dataclass(frozen=True)
class OscSpec:
    input_mode: str      # "close" | "vwap_dev"
    fast: int            # EMA span, M5 bars
    slow: int
    signal: int
    name: str            # e.g. "close-12-26-9" (derived helper)

def compute_oscillator(m5: pd.DataFrame, spec: OscSpec) -> pd.DataFrame
    # columns: fast_line, signal_line, histogram; index = input index (causal EMAs only)

def oscillator_states(osc: pd.DataFrame) -> pd.Series          # categorical, 4 states
def oscillator_crosses(osc: pd.DataFrame) -> pd.DataFrame      # boolean cols, see below
```

**Input modes** (both computed from RTH M5 bars; `m5` must carry `close`, `high`, `low`,
`volume` — the standard loader frame):

- `close`: **PPO** — `fast_line = 100 * (EMA(close, fast) - EMA(close, slow)) / EMA(close, slow)`;
  percentage-normalized so 2021 and 2026 price levels are comparable.
- `vwap_dev`: `dev = 100 * (close - session_vwap) / session_vwap` (session VWAP from the
  existing `indicators/vwap.py`, resets daily — this is the price+volume composite: where price
  trades relative to the volume-weighted session mean). Then
  `fast_line = EMA(dev, fast)`; the dev series already oscillates around zero, so no
  second differencing.

Both modes: `signal_line = EMA(fast_line, signal)`, `histogram = fast_line - signal_line`.
EMAs are plain causal `ewm(span=..., adjust=False)` — no lookahead by construction. Sessions:
EMA state carries across days (no daily reset of the oscillator itself; only the VWAP input
resets) — disclosed simplification, matches how MACD is conventionally run intraday.

**States** (the "group of 4"):

| state | definition |
|---|---|
| `BULL_RUN` | fast > signal AND fast > 0 |
| `BULL_EARLY` | fast > signal AND fast ≤ 0 (bullish cross developing below zero) |
| `BEAR_EARLY` | fast ≤ signal AND fast > 0 (rolling over above zero) |
| `BEAR_RUN` | fast ≤ signal AND fast ≤ 0 |

**Crosses** (`oscillator_crosses`): `bull_cross` (fast crosses above signal),
`bear_cross` (fast crosses below signal), `zero_up`, `zero_down` — one boolean column each,
True only on the crossing bar.

## The skill study (`src/rs_spy/backtest/studies/oscillator_skill_m5.py`)

Pure functions over supplied frames (hermetic tests use synthetic bars); a typer driver script
runs them against real warehouse SPY data.

- **Windows**: train = sessions with date < 2025-01-01 (≈2021-07→2024-12); holdout =
  date ≥ 2025-01-01 (≈2025-01→2026-07). Enforced in code: the study functions take an
  explicit window and the *holdout driver refuses candidate grids* (exactly one `OscSpec`).
- **Candidate grid** (deliberately modest, per the "simple valve" note — 24 candidates):
  `input_mode ∈ {close, vwap_dev}` × `(fast, slow) ∈ {(6,13), (9,21), (12,26), (16,36)}` ×
  `signal ∈ {5, 9, 13}`. For `vwap_dev`, `slow` is unused by the formula and recorded as-is
  (the pair label keeps grid bookkeeping uniform); duplicate-equivalent candidates are fine.
- **Per-candidate measurements** (on SPY M5, per window):
  1. **State-conditioned forward returns**: mean and median SPY forward log-return at
     horizons {12, 24, 78} bars, per state, with n per state. Forward returns computed within
     the session where possible; horizon rows that cross the session close use the next
     session's bars (same convention as the M7.5 trigger-skill study).
  2. **Cross event studies**: forward returns after each cross type (same horizons).
  3. **Separation score** (the selection metric, pre-committed):
     `sep_h = mean_fwd_h(BULL_RUN ∪ BULL_EARLY) - mean_fwd_h(BEAR_RUN ∪ BEAR_EARLY)`.
     Primary: `sep_24`; tie-break: `sep_12`. Eligibility: every one of the 4 states has
     n ≥ 200 in train.
  4. **Trigger composition** (decision-relevant): for the M7.5 LONG trendline-trigger events
     on SPY, forward returns conditioned on the oscillator state at the trigger bar — does
     the oscillator's window select better trigger events than (a) unconditioned and (b) the
     incumbent bias buckets?
- **Incumbent baseline**: the current bias engine's bucket series scored with the *same*
  metric — `sep_h(incumbent) = mean_fwd_h(bullish buckets) - mean_fwd_h(bearish buckets)`
  (bucket→side mapping pinned in the plan from `bias/buckets.py`'s vocabulary). Computed on
  both windows. M7 says ≈0; re-measuring it in-harness makes the comparison apples-to-apples.
- **Winner selection**: highest `sep_24` on TRAIN among eligible candidates. One winner
  overall (not per family). Selection code never sees holdout data.

## The holdout gate (pre-committed, hard)

The single winner passes iff ALL of, on holdout (each state n ≥ 50):

1. `sep_24 > 0` and `sep_12 > 0`;
2. `sep_24(winner) > sep_24(incumbent)` on the same holdout window;
3. `sign(sep_24)` matches train (trivially true given #1 if train winner had positive sep —
   kept explicit so a sign flip reads as the failure it is).

Pass → Phase 2 (a separate spec/plan: `bias_mode` config switch, engine wiring, 500-universe
campaign re-run vs the M10 baseline rows). Fail → the null is published in the ledger +
IMPLEMENTATION.md, the current engine stays, and the milestone closes.

## Artifacts & reporting

- `reports/tuning/oscillator_skill_train.csv` — one row per candidate × state × horizon,
  plus separation-score summary rows.
- `reports/tuning/oscillator_skill_holdout.csv` — the winner + incumbent rows only.
- Per-state practice view for the morning review: a compact table (train + holdout) of state
  occupancy %, mean forward returns, and the trigger-composition lift, printed by the driver
  and saved alongside the CSVs.
- `docs/tuning/ledger.csv` rows + an IMPLEMENTATION.md "M11 Phase 1" section (result-honest,
  pass or fail).

## Error handling

- Study functions validate window boundaries (train max date < 2025-01-01 etc.) and refuse
  empty/short windows.
- The holdout driver takes exactly one candidate (`--spec name`) and errors on anything else.
- Insufficient state occupancy (eligibility floors) excludes a candidate rather than crashing.

## Testing

Hermetic: oscillator math vs hand-computed EMA fixtures; state/cross labeling on constructed
line shapes; separation/forward-return computation on synthetic frames with known answers
(including session-boundary horizon handling); grid/eligibility/selection logic; holdout
single-candidate refusal. Real-data study runs are execution steps.

## Out of scope (v1)

Engine wiring of any kind (Phase 2, only on pass); divergence detection; Ehlers-family
candidates; multi-symbol oscillators (SPY only; QQQ read is a Phase-2 question); any change
to `BacktestConfigM5` or default behavior.
