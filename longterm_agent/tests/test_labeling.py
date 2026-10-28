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
