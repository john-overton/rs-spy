"""Cohort split determinism/stratification + launch/poll orchestration (hermetic)."""
import uuid

from rs_spy.backtest.campaign import (
    VARIANTS,
    campaign_label_re,
    existing_campaign_labels,
    poll_and_launch,
    queued_campaign_runs,
    split_cohorts,
)
from rs_spy.universe import SymbolSpec


class _FakeCursor:
    """Records executed (sql, params) pairs; serves canned dict rows per LIKE pattern."""

    def __init__(self, labels_by_pattern):
        self._labels_by_pattern = labels_by_pattern
        self.executed = []
        self._rows = []

    def execute(self, sql, params):
        self.executed.append((sql, params))
        pattern = params[0]
        self._rows = [{"label": lbl} for lbl in self._labels_by_pattern.get(pattern, [])]

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, labels_by_pattern):
        self.cur = _FakeCursor(labels_by_pattern)

    def cursor(self):
        return self.cur


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


def test_existing_campaign_labels_empty_store_returns_nothing():
    conn = _FakeConn({})
    out = existing_campaign_labels(conn, "jul05", ["baseline", "w12"])
    assert out == []
    # one parameterized query per selected variant, pattern per (tag, variant)
    patterns = [params[0] for _, params in conn.cur.executed]
    assert patterns == ["m10-jul05-baseline-c%", "m10-jul05-w12-c%"]
    assert all("%s" in sql for sql, _ in conn.cur.executed)  # parameterized, not f-string


def test_campaign_label_re_does_not_confuse_tags_differing_only_by_separator():
    # LIKE's unescaped '_' wildcard would match "-" here; the regex must not.
    pattern = campaign_label_re("jul_05", "baseline")
    assert pattern.fullmatch("m10-jul_05-baseline-c1")
    assert not pattern.fullmatch("m10-jul-05-baseline-c1")


def test_campaign_label_re_does_not_confuse_variant_prefixes():
    pattern = campaign_label_re("jul05", "baseline")
    assert pattern.fullmatch("m10-jul05-baseline-c1")
    assert pattern.fullmatch("m10-jul05-baseline-c12")
    assert not pattern.fullmatch("m10-jul05-baseline2-c1")
    # trailing 'c%' in the LIKE pre-filter would over-match this; the label's
    # tag also merely starts the same, both must be rejected.
    assert not pattern.fullmatch("m10-jul05-baseline-cool-w12-c1")


def test_campaign_label_round_trips_through_ui_parse_campaign_label():
    """Pins the cross-module label contract: campaign.py builds
    f"m10-{tag}-{variant}-c{n}" (see create_campaign_runs); ui.data.
    parse_campaign_label must recover the same (tag, variant, n), and
    campaign_label_re must accept exactly that label."""
    from rs_spy.ui.data import parse_campaign_label

    tag, variant, n = "jul05", "w12", 3
    label = f"m10-{tag}-{variant}-c{n}"
    assert campaign_label_re(tag, variant).fullmatch(label)
    assert parse_campaign_label(label) == (tag, variant, n)


class _QueuedFakeCursor:
    """Serves canned (run_id, label, status) rows per LIKE pattern, like
    _FakeCursor but with run_id + status for queued_campaign_runs."""

    def __init__(self, rows_by_pattern):
        self._rows_by_pattern = rows_by_pattern
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))
        self._rows = self._rows_by_pattern.get(params[0], [])

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _QueuedFakeConn:
    def __init__(self, rows_by_pattern):
        self.cur = _QueuedFakeCursor(rows_by_pattern)

    def cursor(self):
        return self.cur


def test_queued_campaign_runs_returns_only_exact_queued_matches():
    rid_good = uuid.uuid4()
    rid_wrong_status = uuid.uuid4()
    rid_spurious = uuid.uuid4()
    conn = _QueuedFakeConn({
        "m10-jul05-baseline-c%": [
            {"run_id": rid_good, "label": "m10-jul05-baseline-c1"},
            # spurious LIKE over-match on a different variant's label
            {"run_id": rid_spurious, "label": "m10-jul05-baseline-cool-w12-c1"},
        ],
    })
    out = queued_campaign_runs(conn, "jul05", ["baseline"])
    assert out == [rid_good]
    assert "status = 'queued'" in conn.cur.executed[0][0]
    del rid_wrong_status  # documents intent: the SQL filters status, not this fake


def test_queued_campaign_runs_covers_multiple_variants():
    rid1, rid2 = uuid.uuid4(), uuid.uuid4()
    conn = _QueuedFakeConn({
        "m10-jul05-baseline-c%": [{"run_id": rid1, "label": "m10-jul05-baseline-c1"}],
        "m10-jul05-w12-c%": [{"run_id": rid2, "label": "m10-jul05-w12-c1"}],
    })
    assert queued_campaign_runs(conn, "jul05", ["baseline", "w12"]) == [rid1, rid2]


def test_existing_campaign_labels_post_filters_spurious_like_matches():
    conn = _FakeConn({
        "m10-jul05-baseline-c%": [
            "m10-jul05-baseline-c1",
            "m10-jul05-baseline-cool-w12-c1",  # spurious LIKE over-match
        ],
    })
    assert existing_campaign_labels(conn, "jul05", ["baseline"]) == ["m10-jul05-baseline-c1"]


def test_existing_campaign_labels_is_per_tag_and_variant():
    # baseline already ran under this tag; w12 has not -- only baseline collides,
    # so launching remaining variants later under the same tag must stay possible.
    conn = _FakeConn({
        "m10-jul05-baseline-c%": ["m10-jul05-baseline-c1", "m10-jul05-baseline-c2"],
    })
    assert existing_campaign_labels(conn, "jul05", ["w12"]) == []
    out = existing_campaign_labels(conn, "jul05", ["baseline", "w12"])
    assert out == ["m10-jul05-baseline-c1", "m10-jul05-baseline-c2"]


class _FakePopen:
    """Stands in for subprocess.Popen: poll() returns the fixed returncode."""

    def __init__(self, returncode):
        self._returncode = returncode

    def poll(self):
        return self._returncode


def test_poll_and_launch_marks_failed_when_process_exits_before_reporting_status():
    # A detached job process that dies before mark_running (bad env, PG
    # unreachable) leaves its run 'queued' forever -- get_run never budges.
    # poll_and_launch must notice the dead process itself and free the slot.
    rid = uuid.uuid4()
    mark_failed_calls = []

    def fake_launch(r):
        return _FakePopen(1)  # process already exited, code 1

    def fake_mark_failed(conn, run_id, error):
        mark_failed_calls.append((conn, run_id, error))

    out = poll_and_launch(
        conn="conn", run_ids=[rid], max_parallel=1, poll_seconds=0,
        launch=fake_launch, sleep=lambda s: None,
        get_run=lambda c, r: {"status": "queued"},
        mark_failed=fake_mark_failed,
    )
    assert out[rid] == "failed"
    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0][1] == rid
    assert "exit code 1" in mark_failed_calls[0][2]


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
