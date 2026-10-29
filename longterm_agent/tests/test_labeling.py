"""Trace auto-labeling: labels restate the grader exactly, carry an honest availability date,
and land on the trace the run was traced under."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core import tracing
from longterm_agent.labeling import results_adapter as ra
from longterm_agent.labeling.base import RUN_NODE, score_name
from longterm_agent.labeling.store import lf_target, score_id
from longterm_agent.label_traces import score_specs


def _row(**over):
    row = {"agent_version": "0.2.0", "ticker": "nvda", "as_of": date(2026, 9, 6),
           "verdict": "LONG", "traded": True, "pit_excluded": None,
           "run_timestamp": datetime(2026, 9, 6, 10, 2, tzinfo=timezone.utc),
           "entry_date": date(2026, 9, 8), "fill_convention": "open_t1",
           "fwd_return_2w": 0.03, "benchmark_return_2w": 0.01,
           "fwd_return_4w": 0.05, "benchmark_return_4w": 0.02,
           "fwd_return_12w": None, "benchmark_return_12w": None,
           "sector_benchmark": "SMH", "sector_benchmark_return_4w": 0.06,
           "fwd_return_stopped": 0.05, "benchmark_return_stopped": 0.02,
           "exit_reason": "horizon", "exit_days": 28, "worst_position_return_4w": -0.04,
           "beta": 1.5, "excess_beta": 0.02,
           "aux_targets": {"shift_outcome": {"label": "persisted", "post_mean_sentiment": 0.2,
                                             "n_days": 9},
                           "fundamentals_realized": {"label": "unknown",
                                                     "note": "no post-horizon filing visible"}}}
    row.update(over)
    return row


def _by_key(labels):
    return {(lb.node, lb.label_key): lb for lb in labels}


def test_long_labels_restate_the_grader_with_position_sign():
    got = _by_key(ra.labels_from_row(_row()))
    assert got[(RUN_NODE, "excess_4w")].value_num == pytest.approx(0.03)
    assert got[(RUN_NODE, "excess_2w")].value_num == pytest.approx(0.02)
    assert got[(RUN_NODE, "hit_4w")].value_num == 1.0
    assert got[(RUN_NODE, "excess_sector_4w")].value_num == pytest.approx(-0.01)
    assert (RUN_NODE, "excess_12w") not in got          # not matured → no label, not a zero
    assert got[(RUN_NODE, "exit_reason")].value_cat == "horizon"


def test_short_flips_the_sign_like_trade_excess():
    got = _by_key(ra.labels_from_row(_row(verdict="SHORT")))
    assert got[(RUN_NODE, "excess_4w")].value_num == pytest.approx(-0.03)
    assert got[(RUN_NODE, "hit_4w")].value_num == 0.0


def test_availability_is_the_day_after_the_last_price_read():
    got = _by_key(ra.labels_from_row(_row()))
    four = got[(RUN_NODE, "excess_4w")]
    assert four.data_cutoff == date(2026, 9, 8) + timedelta(days=28)   # entry + horizon
    assert four.label_available_at == datetime(2026, 10, 7)
    # the shift target reads news from as_of, not from the entry fill
    assert got[("shift_detection", "outcome")].data_cutoff == date(2026, 10, 4)
    # a stop exit is knowable on the stop day, not at the horizon
    stopped = _by_key(ra.labels_from_row(_row(exit_reason="stop", exit_days=9)))
    assert stopped[(RUN_NODE, "excess_stopped")].data_cutoff == date(2026, 9, 17)


def test_missing_entry_date_pushes_the_cutoff_later_never_earlier():
    lb = _by_key(ra.labels_from_row(_row(entry_date=None)))[(RUN_NODE, "excess_4w")]
    assert lb.data_cutoff > date(2026, 9, 6) + timedelta(days=28)
    assert lb.value_json["entry_date_inferred"] is True


def test_decision_moment_is_naive_et():
    lb = ra.labels_from_row(_row())[0]
    assert lb.trace_time == datetime(2026, 9, 6, 6, 2)     # 10:02 UTC = 06:02 EDT


def test_ungradable_target_is_kept_as_unavailable_not_dropped():
    fund = _by_key(ra.labels_from_row(_row()))[("fundamentals_analysis", "realized")]
    assert fund.status == "unavailable" and fund.value_cat is None
    assert fund.reason == "no post-horizon filing visible"


def test_labels_key_the_same_trace_the_live_run_was_traced_under():
    lb = ra.labels_from_row(_row())[0]
    assert lb.trace_key == "live:0.2.0:NVDA:2026-09-06"
    assert tracing.trace_id_for(lb.trace_key) == tracing.run_trace_id(
        "live", "0.2.0", "NVDA", datetime(2026, 9, 6, 6, 0))


def test_trace_seed_format_is_frozen():
    """Every stored label and every Langfuse score is keyed by this string. Changing it
    orphans them all, so it changes only with a migration."""
    from langfuse import Langfuse
    assert tracing.run_trace_id("live", "3.1", "NVDA", date(2026, 11, 1)) == \
        Langfuse.create_trace_id(seed="live:3.1:NVDA:2026-11-01")


def test_every_label_has_a_score_config_and_known_categories():
    specs = score_specs()
    rows = [_row(), _row(verdict="SHORT", exit_reason="stop", exit_days=5,
                         fwd_return_12w=0.1, benchmark_return_12w=0.05)]
    for row in rows:
        for lb in ra.labels_from_row(row):
            spec = specs[score_name(lb.node, lb.label_key)]
            if lb.status != "final":
                continue
            if spec.data_type == "CATEGORICAL":
                assert lb.value_cat in {c for c, _ in spec.categories}
            else:
                assert lb.value_num is not None


def test_score_id_survives_a_new_labeler_version_and_value():
    a = {"trace_key": "live:0.2.0:NVDA:2026-09-06", "node": "run", "span": "",
         "label_key": "excess_4w", "labeler_version": "1", "value_num": 0.03}
    b = {**a, "labeler_version": "2", "value_num": 0.031}
    assert score_id(a) == score_id(b) and len(score_id(a)) == 32
    assert score_id(a) != score_id({**a, "label_key": "excess_2w"})


def test_sync_target_is_the_project_not_the_url_and_hides_the_key():
    t = lf_target("pk-lf-0000")
    assert t == lf_target("pk-lf-0000") and len(t) == 16 and "pk-lf" not in t
    assert t != lf_target("pk-lf-0001")


def test_run_window_ends_at_the_persist_stamp():
    from longterm_agent.labeling.langfuse_sync import run_window
    end = datetime(2026, 9, 6, 10, 2, tzinfo=timezone.utc)
    start, got_end = run_window({"run_timestamp": end, "execution_time_seconds": 120,
                                 "created_at": datetime(2026, 9, 6, 10, 3)})
    assert got_end == end and start == end - timedelta(seconds=120)
    # no results row: the snapshot's own stamp is the DB clock, i.e. UTC
    start, got_end = run_window({"run_timestamp": None, "execution_time_seconds": None,
                                 "created_at": datetime(2026, 9, 6, 10, 3)})
    assert got_end == datetime(2026, 9, 6, 10, 3, tzinfo=timezone.utc)


def test_sdk_internals_used_for_past_spans_still_exist():
    """rebuild_trace starts spans in the past through the SDK's own OTel tracer, because the
    public API always stamps now(). An SDK upgrade that renames these must fail here, not
    in the Sunday job."""
    from langfuse import Langfuse
    lf = Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test",
                  base_url="http://127.0.0.1:9", tracing_enabled=False)
    for name in ("_otel_tracer", "_environment", "_create_remote_parent_span",
                 "_create_observation_from_otel_span"):
        assert hasattr(lf, name), name
    from langfuse._client.attributes import LangfuseOtelSpanAttributes
    assert LangfuseOtelSpanAttributes.AS_ROOT


def test_a_failing_label_job_never_breaks_grading(monkeypatch):
    from longterm_agent import auto_longterm_agent, label_traces

    def boom(argv):
        raise RuntimeError("langfuse down")
    monkeypatch.setattr(label_traces, "main", boom)
    monkeypatch.setattr(auto_longterm_agent.oplog, "error", lambda *a, **k: None)
    auto_longterm_agent.Ops.label_traces(SimpleNamespace())   # must not raise


# --- stage 2 labelers ----------------------------------------------------------------------
def _series(start, closes):
    return [(start + timedelta(days=i), c) for i, c in enumerate(closes)]


def test_sigma_uses_only_the_year_before_as_of():
    from longterm_agent.labeling.counterfactual import sigma_4w
    as_of = date(2026, 9, 1)
    start = as_of - timedelta(days=200)
    bench = _series(start, [100.0] * 200)
    flat = _series(start, [50.0] * 200)
    assert sigma_4w(flat, bench, as_of) is None            # zero spread is no unit
    wavy = _series(start, [50.0 * (1.02 if i % 2 else 0.98) ** (i % 7) for i in range(200)])
    sd = sigma_4w(wavy, bench, as_of)
    # a crash ON as_of must not move the normalizer of the run that decided before it
    crashed = wavy + [(as_of, 1.0)]
    assert sd and sigma_4w(crashed, bench + [(as_of, 100.0)], as_of) == sd
    assert sigma_4w(wavy[:60], bench, as_of) is None        # too few 20-session windows


def test_opportunity_is_symmetric_around_one_sigma():
    from longterm_agent.labeling.counterfactual import opportunity
    assert [opportunity(z) for z in (1.0, 0.99, -0.99, -1.0)] == ["long", "none", "none", "short"]


def test_gate_value_is_what_blocking_saved():
    from longterm_agent.labeling.decision_deltas import gate_call
    assert gate_call(False, -0.05) == (0.05, "blocked_loss")   # blocked a loser: +5%
    assert gate_call(False, 0.03) == (-0.03, "blocked_win")
    assert gate_call(True, 0.03) == (0.0, "passed_win")
    assert gate_call(True, -0.03) == (0.0, "passed_loss")


def test_reflection_passes_parse_and_an_outage_is_not_a_judgment():
    from longterm_agent.labeling.decision_deltas import parse_pass, reflection_change
    first = parse_pass("LONG conviction=0.65: AVGO's negative news shift …")
    failed = parse_pass("NEUTRAL conviction=0.0: (synthesis unavailable)")
    assert first == ("LONG", 0.65, False) and failed == ("NEUTRAL", 0.0, True)
    assert reflection_change(first, failed) == "failed"
    assert reflection_change(first, ("SHORT", 0.6, False)) == "direction"
    assert reflection_change(first, ("LONG", 0.62, False)) == "none"
    assert reflection_change(first, ("LONG", 0.5, False)) == "conviction"
    assert parse_pass("garbage") is None


def test_bucket_window_excess_reads_closes_on_or_before_the_edges():
    from longterm_agent.labeling.map_buckets import parse_week, window_excess
    start, end = parse_week("2026-08-06..2026-08-13")
    stock = [(date(2026, 8, 5), 100.0), (date(2026, 8, 6), 100.0), (date(2026, 8, 12), 110.0),
             (date(2026, 8, 14), 999.0)]                       # after the window: ignored
    bench = [(date(2026, 8, 6), 200.0), (date(2026, 8, 13), 202.0)]
    assert window_excess(stock, bench, start, end) == (0.09, date(2026, 8, 13))
    assert window_excess(stock[:1], bench, start, end) is None   # no close inside the window
    assert parse_week("not a week") is None


def test_session_labels_score_the_batch_session_under_a_bare_name():
    from longterm_agent.labeling import cohorts
    from longterm_agent.labeling.base import SESSION_NODE
    assert cohorts.session_id(date(2026, 9, 28)) == "batch-2026-09-28"
    assert score_name(SESSION_NODE, "cohort_excess_4w") == "cohort_excess_4w"
    assert "cohort_excess_4w" in score_specs()


def test_session_score_request_has_no_trace():
    from longterm_agent.labeling.langfuse_sync import _score
    specs = score_specs()
    label = {"trace_kind": "lt_session", "trace_key": "session:0.2.0:batch-2026-09-28",
             "trace_id": "0" * 32, "node": "session", "span": "", "label_key": "cohort_hit_4w",
             "value_num": 0.5, "value_cat": None, "labeler": "cohorts", "labeler_version": "1",
             "data_cutoff": date(2026, 10, 26), "label_available_at": datetime(2026, 10, 27),
             "ticker": None, "as_of": date(2026, 9, 28),
             "value_json": {"session_id": "batch-2026-09-28"}}
    req = _score(label, None, "live", specs, {})
    assert req.session_id == "batch-2026-09-28" and req.trace_id is None
    assert req.timestamp.isoformat().startswith("2026-10-27T00:00:00-04:00")


def test_every_labeler_shares_one_collect_signature():
    import inspect
    from longterm_agent.label_traces import LABELERS
    for lab in LABELERS:
        assert list(inspect.signature(lab.collect).parameters)[:2] == ["cur", "ctx"], lab.NAME


def test_categorical_configs_are_valid_for_langfuse():
    """Langfuse rejects a score config whose category values (or labels) repeat — found the
    hard way on the server: risk_gate.call_4w had 1/1/-1/-1 and the whole sync stopped."""
    for name, spec in score_specs().items():
        if spec.data_type != "CATEGORICAL":
            assert not spec.categories, name
            continue
        labels = [lb for lb, _ in spec.categories]
        values = [v for _, v in spec.categories]
        assert len(set(labels)) == len(labels) and len(set(values)) == len(values), name
