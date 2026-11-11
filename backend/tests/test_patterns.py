from datetime import datetime, timedelta, timezone

from app.models import Event
from app.patterns import collapse, mine

UTC = timezone.utc
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def ev(i, minute, msg, svc="orders", sev="info"):
    return Event(id=f"e{i}", ts=T0 + timedelta(minutes=minute, seconds=i % 50), source="log", service=svc,
                 severity=sev, message=msg, incident_id="inc")


def test_drain_groups_lines_with_same_shape():
    events = [ev(i, 0, f"fetched order o-{10000 + i} in {20 + i}ms") for i in range(30)]
    events += [ev(100 + i, 1, f"created order o-{20000 + i} for user u-{300 + i} total={199 + i}") for i in range(10)]
    events += [ev(200, 1, "fetched order o-1 in 5ms", svc="payments")]  # same words, other service
    pats = mine(events)
    assert len({pats[e.id][0] for e in events if e.service == "orders"}) == 2
    assert pats["e200"][0].startswith("payments#")
    template = pats["e0"][1]
    assert "<ID>" in template and "<NUM>" in template and "o-10000" not in template


def test_collapse_classifies_new_increased_unchanged():
    events = []
    i = 0
    for m in range(20):  # baseline: minutes 0-9, incident: minutes 10-19
        for _ in range(3):
            events.append(ev(i, m, f"health check ok uptime={i}s")); i += 1
        if m < 10 or m % 2 == 0:
            events.append(ev(i, m, f"cache miss for key k{i}")); i += 1
        if m >= 10:
            for _ in range(4):
                events.append(ev(i, m, f"cache miss for key k{i}")); i += 1
            events.append(ev(i, m, f"KeyError: 'discount_code' in apply_promotions order=o-{i}", sev="error")); i += 1
    events.sort(key=lambda e: e.ts)
    items = collapse(events, mine(events), T0 + timedelta(minutes=10), T0, T0 + timedelta(minutes=20))
    by_change = {p.change: p for p in items}
    assert by_change["new"].severity == "error" and by_change["new"].count == 10
    assert by_change["increased"].template.startswith("cache miss")
    assert by_change["unchanged"].template.startswith("health check")
    assert sum(p.count for p in items) == len(events)  # nothing lost when collapsing


def test_noise_reduction_on_demo_logs(demo_runs):
    from app.pipeline import analyze

    st, inc, truth = demo_runs["bad_deploy"]
    a = analyze(st, inc)
    assert a.log_lines > 30000
    assert a.timeline_items < 40
    assert len(a.patterns) < 30
