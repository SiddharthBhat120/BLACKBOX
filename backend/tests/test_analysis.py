from datetime import datetime

from app.pipeline import analyze


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_bad_deploy_found_and_timed(demo_runs):
    st, inc, truth = demo_runs["bad_deploy"]
    a = analyze(st, inc)
    assert a.first_failing_service == "orders"
    top = a.triggers[0]
    assert top.service == "orders" and "v1.8.0" in top.title
    assert any("before the first errors" in e for e in top.evidence)
    assert abs((a.start - ts(truth["start"])).total_seconds()) < 120
    assert abs((a.detection - ts(truth["detect"])).total_seconds()) < 60
    assert a.recovery and abs((a.recovery - ts(truth["recovery"])).total_seconds()) < 180
    assert a.time_to_detect_min is not None and a.time_to_detect_min >= 0
    kinds = {m.kind for m in a.key_moments}
    assert {"first_error", "error_spike", "alert_fired", "first_human", "mitigation", "recovery", "change"} <= kinds
    for m in a.key_moments:
        assert m.event_ids and m.reason


def test_outside_cause_wins_when_nothing_changed(demo_runs):
    st, inc, truth = demo_runs["slow_dependency"]
    a = analyze(st, inc)
    assert truth["trigger"] is None
    assert a.triggers[0].event_id is None
    assert a.first_failing_service == "payments"


def test_slow_burn_trigger_far_before_start(demo_runs):
    st, inc, truth = demo_runs["disk_full"]
    a = analyze(st, inc)
    top = a.triggers[0]
    assert "v3.2.0" in top.title and top.minutes_before_start > 15
    assert any("new log pattern" in e for e in top.evidence)
