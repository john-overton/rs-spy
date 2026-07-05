"""Cohort split determinism/stratification + launch/poll orchestration (hermetic)."""
import uuid

from rs_spy.backtest.campaign import VARIANTS, poll_and_launch, split_cohorts
from rs_spy.universe import SymbolSpec


def _specs():
    # 8 symbols, 2 sectors interleaved so stratification is observable
    return [SymbolSpec(symbol=f"T{i}", sector="Tech") for i in range(4)] + \
           [SymbolSpec(symbol=f"F{i}", sector="Fin") for i in range(4)]


def test_split_cohorts_is_deterministic_and_sector_stratified():
    a = split_cohorts(_specs(), n_cohorts=2)
    b = split_cohorts(list(reversed(_specs())), n_cohorts=2)
    assert a == b  # input order must not matter
    for cohort in a:
        sectors = {"Tech" if s.startswith("T") else "Fin" for s in cohort}
        assert sectors == {"Tech", "Fin"}  # both sectors present in every cohort
    assert sorted(a[0] + a[1]) == sorted(s.symbol for s in _specs())  # partition


def test_variants_cover_the_campaign_matrix():
    assert set(VARIANTS) == {"baseline", "w12", "w24", "hold2", "shorts"}
    assert VARIANTS["baseline"] == {}
    assert VARIANTS["w12"] == {"rrs_m5_window": 12}
    assert VARIANTS["shorts"] == {"shorts_enabled": True}


def test_poll_and_launch_respects_max_parallel_and_runs_all():
    ids = [uuid.uuid4() for _ in range(4)]
    state = {rid: "queued" for rid in ids}
    launched, max_live = [], 0

    def fake_launch(rid):
        state[rid] = "running"
        launched.append(rid)

    def fake_get_run(conn, rid):
        return {"status": state[rid]}

    def fake_sleep(_secs):
        # one running job finishes per poll tick
        live = [r for r in ids if state[r] == "running"]
        nonlocal max_live
        max_live = max(max_live, len(live))
        if live:
            state[live[0]] = "succeeded"

    out = poll_and_launch(
        conn=None, run_ids=ids, max_parallel=2, poll_seconds=0,
        launch=fake_launch, sleep=fake_sleep, get_run=fake_get_run,
    )
    assert launched == ids            # every run launched, FIFO
    assert max_live <= 2              # parallelism cap respected
    assert set(out.values()) == {"succeeded"}


def test_poll_and_launch_reports_failed_runs_without_hanging():
    rid = uuid.uuid4()
    state = {rid: "queued"}

    def fake_launch(r):
        state[r] = "failed"

    out = poll_and_launch(
        conn=None, run_ids=[rid], max_parallel=1, poll_seconds=0,
        launch=fake_launch, sleep=lambda s: None,
        get_run=lambda c, r: {"status": state[r]},
    )
    assert out[rid] == "failed"
