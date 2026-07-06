# M11 Phase 1: cycle-oscillator skill study — decision tables

Archived, verbatim-where-possible record of the tables quoted in
`IMPLEMENTATION.md`'s "M11 Phase 1: cycle oscillator skill study" section and the
`docs/tuning/ledger.csv` rows `m11-train-winner` / `m11-holdout-gate`. Written during the
final whole-branch review's fix-before-merge pass — the driver was **not** re-run to produce
this file (single-shot holdout protocol; train sweep also not re-run).

**Provenance**: the raw terminal transcripts of `python scripts/run_oscillator_study.py train`
and `... holdout --spec close-6-13-5` were not captured to a file during the original session
(`.superpowers/sdd/train_output.txt` / `holdout_output.txt` do not exist in this worktree).
Sections below are sourced either from the two committed CSVs
(`reports/tuning/oscillator_skill_train.csv`, `reports/tuning/oscillator_skill_holdout.csv` —
real, re-derivable data, computed by `scripts/run_oscillator_study.py` against
`data/warehouse.duckdb`) or, where the CSVs don't carry the number, reconstructed from the
session's contemporaneous notes (`IMPLEMENTATION.md`'s M11 section as originally written and
the `docs/tuning/ledger.csv` `m11-train-winner` / `m11-holdout-gate` rows). Every
reconstructed-from-notes table is labeled as such, and gaps (numbers that were never recorded
anywhere, e.g. median forward returns for the trigger-composition tables, or the full
per-state train breakdown for the winner) are marked "not recorded" rather than invented.

---

## (a) Train leaderboard, top-10 by `sep_24`

**Source: real data**, `reports/tuning/oscillator_skill_train.csv` (24-candidate grid, all
eligible), sorted by `sep_24` descending — reproduces exactly what
`scripts/run_oscillator_study.py train` prints as `== leaderboard (train) ==`.

| name | input_mode | fast | slow | signal | sep_12 | sep_24 | sep_78 | min_state_n | eligible |
|---|---|---|---|---|---|---|---|---|---|
| close-6-13-5 | close | 6 | 13 | 5 | 1.634202e-04 | 8.337500e-05 | -6.662727e-05 | 12085 | True |
| vwap_dev-12-26-13 | vwap_dev | 12 | 26 | 13 | 8.176361e-05 | 6.558312e-05 | -7.783052e-06 | 11899 | True |
| vwap_dev-16-36-13 | vwap_dev | 16 | 36 | 13 | 8.045612e-05 | 6.424699e-05 | 4.232336e-06 | 12259 | True |
| close-6-13-9 | close | 6 | 13 | 9 | 1.556720e-04 | 5.564420e-05 | -6.049253e-05 | 10510 | True |
| vwap_dev-16-36-9 | vwap_dev | 16 | 36 | 9 | 7.372380e-05 | 5.154442e-05 | -1.374279e-05 | 12964 | True |
| vwap_dev-9-21-13 | vwap_dev | 9 | 21 | 13 | 7.638070e-05 | 3.814511e-05 | -4.311179e-05 | 11468 | True |
| close-6-13-13 | close | 6 | 13 | 13 | 1.366876e-04 | 3.644570e-05 | -1.208176e-05 | 9469 | True |
| close-9-21-5 | close | 9 | 21 | 5 | 1.310419e-04 | 2.379411e-05 | -3.772843e-05 | 13182 | True |
| vwap_dev-12-26-9 | vwap_dev | 12 | 26 | 9 | 5.618743e-05 | 1.834941e-05 | -7.695156e-05 | 12634 | True |
| vwap_dev-16-36-5 | vwap_dev | 16 | 36 | 5 | 5.856573e-05 | 1.196651e-05 | -7.700351e-05 | 13751 | True |

**Grid-boundary note** (final-review observation): restrict to the `close` family and sort by
speed (fast/slow/signal ascending) — `sep_24` decreases monotonically from the winner
(`close-6-13-5`, `8.3e-5`) down to the slowest candidate (`close-16-36-13`, `-1.26e-4`), with
no interior maximum. The winner sits at the fastest corner of the grid; the boundary chose the
winner, not an optimum inside it.

---

## (b) Winner per-state (train) — `close-6-13-5`

**Reconstructed from notes — INCOMPLETE.** `state_skill_table` for the train-window winner
was printed to the terminal (`== winner per-state (train) ==`) but not saved to a CSV or
captured to a file; only the aggregate `separation_scores` survived in
`docs/tuning/ledger.csv` (`m11-train-winner`) and `IMPLEMENTATION.md`. The individual
per-(state, horizon) `n`/`mean`/`median` cells that `separation_scores` was computed from are
**not recoverable** without re-running the train sweep, which this fix-pass is instructed not
to do.

What is known (aggregate, n-weighted bull-minus-bear separation, from `separation_scores`):

| metric | value |
|---|---|
| `sep_12` | 1.634202e-04 |
| `sep_24` | 8.337500e-05 |
| `sep_78` | -6.662727e-05 |
| `min_state_n` | 12085 |

Note `sep_78 < 0`: the bull/bear read inverts at the 78-bar horizon on train, same direction
as holdout (see section (f)).

---

## (c) Winner crosses (train) — `close-6-13-5`

**Not recorded.** `cross_skill_table` (forward returns conditioned on `bull_cross` /
`bear_cross` / `zero_up` / `zero_down` events) was printed as `== winner crosses (train) ==`
but no numbers from it were captured in the ledger, `IMPLEMENTATION.md`, or any committed CSV.
This table cannot be reconstructed from session notes; it would require re-running the train
sweep.

---

## (d) Incumbent (train, same metric) — table + scores

**Partially reconstructed from notes.** The per-state breakdown (`== incumbent buckets
(train, same metric) ==`) was terminal-only and not captured. The aggregate scores were
recorded in `docs/tuning/ledger.csv` (`m11-train-winner`) and `IMPLEMENTATION.md`:

| metric | value |
|---|---|
| `sep_12` | 1.52e-4 |
| `sep_24` | 2.6e-5 |
| `sep_78` | 6.5e-4 |

All three horizons positive on train — the incumbent's problem (per section (f)) shows up
later, on holdout, not here. Structurally (per `incumbent_skill`'s bucket→state mapping,
`src/rs_spy/backtest/studies/oscillator_skill_m5.py`), the incumbent's `BULL_EARLY` and
`BEAR_EARLY` state rows are always `n=0` (the current bias engine has no EARLY-equivalent
bucket) — visible directly in the holdout CSV (section (f)) and presumed identical in
structure on train, though the train `n` values themselves were not recorded.

---

## (e) Train LONG-trigger composition, by winner state — `close-6-13-5`

**Reconstructed from notes.** `trigger_composition_table` (`== LONG-trigger composition by
winner state (train) ==`) — n and mean forward return were captured in this fix-pass's
dispatch; **median forward return was not recorded** and is omitted (marked `n/a` below). `n`
was recorded only at `h=12`; `h=24`/`h=78` counts were not separately noted (expected to be at
or very slightly below the `h=12` counts, per the same near-window-end truncation pattern
visible in the holdout CSV, but not independently confirmed here).

| state | horizon_bars | n | mean_fwd_return | median_fwd_return |
|---|---|---|---|---|
| ALL | 12 | 1112 | 2.36e-04 | n/a |
| BULL_RUN | 12 | 959 | 2.42e-04 | n/a |
| BULL_EARLY | 12 | 68 | 5.21e-04 | n/a |
| BEAR_RUN | 12 | 5 | -5.68e-04 | n/a |
| BEAR_EARLY | 12 | 80 | -3.1e-05 | n/a |
| ALL | 24 | n/a (not recorded; ≈1112) | 3.12e-04 | n/a |
| BULL_RUN | 24 | n/a (not recorded; ≈959) | 2.73e-04 | n/a |
| BULL_EARLY | 24 | n/a (not recorded; ≈68) | 7.9e-04 | n/a |
| BEAR_RUN | 24 | n/a (not recorded; ≈5) | 3.137e-03 | n/a |
| BEAR_EARLY | 24 | n/a (not recorded; ≈80) | 2.02e-04 | n/a |
| ALL | 78 | n/a (not recorded; ≈1112) | 1.007e-03 | n/a |
| BULL_RUN | 78 | n/a (not recorded; ≈959) | 9.38e-04 | n/a |
| BULL_EARLY | 78 | n/a (not recorded; ≈68) | 2.181e-03 | n/a |
| BEAR_RUN | 78 | n/a (not recorded; ≈5) | -1.483e-03 | n/a |
| BEAR_EARLY | 78 | n/a (not recorded; ≈80) | 1.001e-03 | n/a |

Sanity check on the recorded `n`: `959 + 68 + 5 + 80 = 1112 = ALL` — consistent with
`trigger_composition_table`'s row structure (ALL unconditioned, then the 4 states partition
it).

This is the table behind `IMPLEMENTATION.md`'s train headline: `BULL_EARLY` triggers (n=68)
had `fwd_78 = 2.18e-3` vs. `ALL` triggers' `1.0e-3` (≈2x); `BEAR_RUN` triggers (n=5, too small
to weigh) were negative at 12/78 bars.

---

## (f) Holdout — per-state, incumbent, composition, scores, checks, VERDICT

### Per-state (winner, `close-6-13-5`) — real data, `reports/tuning/oscillator_skill_holdout.csv`

| state | horizon_bars | n | mean_fwd_return | median_fwd_return |
|---|---|---|---|---|
| BULL_RUN | 12 | 8953 | 1.592937e-04 | 1.840161e-04 |
| BULL_EARLY | 12 | 4976 | 4.681813e-05 | 8.191414e-05 |
| BEAR_RUN | 12 | 7942 | 2.026202e-04 | 2.258946e-04 |
| BEAR_EARLY | 12 | 7111 | 8.095306e-06 | 2.199029e-04 |
| BULL_RUN | 24 | 8953 | 2.862358e-04 | 4.441381e-04 |
| BULL_EARLY | 24 | 4969 | 1.430034e-04 | 2.922811e-04 |
| BEAR_RUN | 24 | 7937 | 3.332196e-04 | 2.913626e-04 |
| BEAR_EARLY | 24 | 7111 | 1.073879e-04 | 4.267252e-04 |
| BULL_RUN | 78 | 8937 | 5.031594e-04 | 1.050949e-03 |
| BULL_EARLY | 78 | 4961 | 1.037622e-03 | 7.817358e-04 |
| BEAR_RUN | 78 | 7922 | 1.055569e-03 | 9.877949e-04 |
| BEAR_EARLY | 78 | 7096 | 5.602309e-04 | 9.725304e-04 |

**Inversion note**: `BEAR_RUN`'s mean exceeds `BULL_RUN`'s at both `h=12` (2.026202e-04 vs.
1.592937e-04) and `h=24` (3.332196e-04 vs. 2.862358e-04). See `IMPLEMENTATION.md` point 7 —
the positive pooled `sep_h` is an EARLY-state / n-weighting artifact, not `RUN`-state
ordering.

### Per-state (incumbent) — real data, same CSV

| state | horizon_bars | n | mean_fwd_return | median_fwd_return |
|---|---|---|---|---|
| BULL_RUN | 12 | 11896 | 9.607242e-05 | 1.921154e-04 |
| BULL_EARLY | 12 | 0 | NaN | NaN |
| BEAR_RUN | 12 | 3364 | 2.510657e-04 | 1.876179e-04 |
| BEAR_EARLY | 12 | 0 | NaN | NaN |
| BULL_RUN | 24 | 11896 | 2.104240e-04 | 4.643856e-04 |
| BULL_EARLY | 24 | 0 | NaN | NaN |
| BEAR_RUN | 24 | 3364 | 3.010947e-04 | 2.455739e-04 |
| BEAR_EARLY | 24 | 0 | NaN | NaN |
| BULL_RUN | 78 | 11867 | 6.121160e-04 | 9.554140e-04 |
| BULL_EARLY | 78 | 0 | NaN | NaN |
| BEAR_RUN | 78 | 3364 | 1.100821e-03 | 1.213119e-03 |
| BEAR_EARLY | 78 | 0 | NaN | NaN |

`BULL_EARLY`/`BEAR_EARLY` are always `n=0` for the incumbent — it has no EARLY-equivalent
bucket (`incumbent_skill`'s bull/bear-bucket→`RUN`-state mapping).

### LONG-trigger composition (holdout) — **reconstructed from notes**

Same caveats as (e): n and mean captured, median not recorded; `n` recorded independently at
`h=12` and `h=78` (both given), `h=24` not recorded (interpolated as `n/a`, expected ≈478-479).

| state | horizon_bars | n | mean_fwd_return | median_fwd_return |
|---|---|---|---|---|
| ALL | 12 | 479 | 2.04e-04 | n/a |
| BULL_RUN | 12 | 388 | 1.68e-04 | n/a |
| BULL_EARLY | 12 | 38 | 7.84e-04 | n/a |
| BEAR_RUN | 12 | 5 | 2.036e-03 | n/a |
| BEAR_EARLY | 12 | 48 | -1.62e-04 | n/a |
| ALL | 24 | n/a (not recorded; ≈478-479) | 5.96e-04 | n/a |
| BULL_RUN | 24 | n/a (not recorded; ≈387-388) | 5.8e-04 | n/a |
| BULL_EARLY | 24 | n/a (not recorded; ≈38) | 9.09e-04 | n/a |
| BEAR_RUN | 24 | n/a (not recorded; ≈5) | 3.027e-03 | n/a |
| BEAR_EARLY | 24 | n/a (not recorded; ≈48) | 2.28e-04 | n/a |
| ALL | 78 | 478 | 1.0e-03 | n/a |
| BULL_RUN | 78 | 387 | 1.068e-03 | n/a |
| BULL_EARLY | 78 | 38 | 5.1e-05 | n/a |
| BEAR_RUN | 78 | 5 | 3.767e-03 | n/a |
| BEAR_EARLY | 78 | 48 | 9.18e-04 | n/a |

Sanity check: `388 + 38 + 5 + 48 = 479 = ALL` (h=12); `387 + 38 + 5 + 48 = 478 = ALL` (h=78).
This is the table behind the "trigger-composition standout did not replicate" finding: train's
`BULL_EARLY` lift (n=68, `fwd_78=2.18e-3` vs. `ALL` `1.0e-3`, ≈2x) shrank to n=38,
`fwd_78=5.1e-5` vs. `ALL` `1.0e-3` on holdout.

### Scores, checks, VERDICT — real / ledger data

Winner (`close-6-13-5`) holdout scores:

| metric | value |
|---|---|
| `sep_12` | 8.4e-6 |
| `sep_24` | 8.61e-6 |
| `sep_78` | -1.276e-04 |
| `min_state_n` | 4961 |

Incumbent holdout scores:

| metric | value |
|---|---|
| `sep_12` | -1.55e-4 |
| `sep_24` | -9.07e-5 |
| `sep_78` | -4.887e-04 |

Train `sep_24` (for the `sign_consistent` check): `8.3375e-05`.

| check | result |
|---|---|
| `sep_24_pos` | PASS (`8.6e-6 > 0`) |
| `sep_12_pos` | PASS (`8.4e-6 > 0`) |
| `beats_incumbent` | PASS (`8.6e-6 > -9.1e-5`) |
| `sign_consistent` | PASS (holdout and train `sep_24` both positive) |
| `min_n_ok` | PASS (`min_state_n=4961 ≥ 50`) |

**VERDICT: PASS** — all 5 pre-committed checks. See `IMPLEMENTATION.md`'s "honest
interpretation" list (including the final-review additions: statistical-noise-floor framing,
the per-state inversion, and the grid-boundary observation) for why this PASS is weak
directional evidence rather than confirmed signal.
