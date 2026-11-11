from datetime import datetime, timedelta, timezone

from app.models import Event
from app.timeutil import to_utc

UTC = timezone.utc


def test_timezones_are_normalised_to_utc():
    assert to_utc("2026-11-10T14:30:00+05:30") == datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    assert to_utc("2026-11-10 14:30:00+0530") == datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    assert to_utc("2026-11-10 09:00:00,250").microsecond == 250000
    assert to_utc("2026-11-10T09:00:00Z").tzinfo == UTC
    assert to_utc("2026-11-10 14:30:00", default_tz="Asia/Kolkata").hour == 9
    assert to_utc(1794301200) == datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    assert to_utc(1794304800000) == to_utc(1794304800)  # milliseconds detected


def _ev(i, ts, **kw):
    return Event(id=f"e{i}", ts=ts, source=kw.get("source", "log"), service=kw.get("service", "orders"),
                 severity=kw.get("severity", "info"), message=kw.get("message", f"msg {i}"), incident_id="inc")


def test_out_of_order_arrival_is_sorted(store):
    t0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    store.add_events([_ev(3, t0 + timedelta(seconds=30)), _ev(1, t0), _ev(2, t0 + timedelta(seconds=10))])
    store.add_events([_ev(0, t0 - timedelta(seconds=5))])  # arrives last, happened first
    assert [e.id for e in store.query("inc")] == ["e0", "e1", "e2", "e3"]


def test_query_filters(store):
    t0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    store.add_events([
        _ev(1, t0, service="orders", severity="info", message="fetched order"),
        _ev(2, t0 + timedelta(minutes=1), service="payments", severity="error", message="provider timeout"),
        _ev(3, t0 + timedelta(minutes=2), service="orders", severity="critical", message="db down"),
        _ev(4, t0 + timedelta(minutes=3), source="deploy", service="orders", message="Deploy orders v2"),
    ])
    assert [e.id for e in store.query("inc", service="orders")] == ["e1", "e3", "e4"]
    assert [e.id for e in store.query("inc", min_severity="error")] == ["e2", "e3"]
    assert [e.id for e in store.query("inc", source="deploy")] == ["e4"]
    assert [e.id for e in store.query("inc", text="timeout")] == ["e2"]
    window = store.query("inc", start=t0 + timedelta(seconds=30), end=t0 + timedelta(minutes=2))
    assert [e.id for e in window] == ["e2", "e3"]
    assert store.query("inc", ids=["e3", "nope"])[0].id == "e3"
