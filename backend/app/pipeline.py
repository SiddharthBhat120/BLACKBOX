"""Phase 5: put it together. Mine patterns, find key moments, time the incident, rank triggers."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .correlate import first_failing, rank_triggers
from .metrics import minute, per_minute, recovered_at, spikes
from .models import Event, IncidentAnalysis, KeyMoment, PatternItem
from .patterns import collapse, mine
from .store import Store

MITIGATION_WORDS = re.compile(
    r"\b(roll(ed)?[ -]?back|revert(ed)?|restor(e|ed)|restart(ed)?|fail(ed)? ?over|switch(ed|ing)? .* to|"
    r"scaled|cleared|disabled|turned off|set \w+=)", re.I)
INITIAL_BASELINE = timedelta(minutes=15)


def _mins(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds() / 60, 1) if a and b else None


def analyze(store: Store, incident_id: str) -> IncidentAnalysis:
    events = store.query(incident_id)
    if not events:
        raise ValueError("This incident has no events yet. Ingest logs, alerts, deploys or chat first.")
    logs = [e for e in events if e.source == "log"]
    ws, we = events[0].ts, events[-1].ts

    # 1. Patterns for every log line (stored so the UI and queries can use them).
    patterns = mine(logs)
    store.set_patterns([(eid, pid) for eid, (pid, _) in patterns.items()])
    for e in logs:
        e.pattern_id = patterns[e.id][0]

    # 2. First pass: a short initial baseline to find roughly when things went wrong.
    metrics = per_minute(logs, ws, we)
    first_base_end = min(ws + INITIAL_BASELINE, ws + (we - ws) / 3)
    rough_start = None
    for svc, series in metrics.items():
        for t, _, _ in spikes(series, first_base_end, "error")[:1]:
            rough_start = t if rough_start is None or t < rough_start else rough_start
    # Baseline = everything well before the trouble; if nothing spiked, the first third.
    baseline_end = (rough_start - timedelta(minutes=3)) if rough_start and rough_start - timedelta(minutes=3) > ws + timedelta(minutes=5) \
        else ws + (we - ws) / 3

    # 3. Noise reduction against that baseline.
    items = collapse(logs, patterns, baseline_end, ws, we)
    by_id = {e.id: e for e in events}
    moments: list[KeyMoment] = []

    def add(kind: str, ts: datetime, service: str, title: str, reason: str, score: float, ids: list[str]) -> KeyMoment:
        m = KeyMoment(id=f"k{len(moments) + 1}", ts=ts, kind=kind, service=service, title=title, reason=reason,
                      score=round(score, 2), event_ids=ids)
        moments.append(m)
        return m

    # 4. Key moments.
    service_first: dict[str, tuple[datetime, str]] = {}
    error_patterns: list[PatternItem] = []
    for p in items:
        if p.change not in ("new", "increased"):
            continue
        is_err = p.severity in ("error", "critical")
        first_ev = by_id[p.first_event_id]
        if is_err:
            error_patterns.append(p)
            add("first_error", first_ev.ts, p.service, f"New error in {p.service}" if p.change == "new" else f"More errors in {p.service}",
                f"\"{p.template[:90]}\" {'first seen' if p.change == 'new' else 'rose from ' + str(p.baseline_count) + ' in baseline'}; {p.incident_count} times in the incident window",
                min(1.0, 0.5 + p.incident_count / 400), [p.first_event_id])
            if p.service not in service_first or first_ev.ts < service_first[p.service][0]:
                service_first[p.service] = (first_ev.ts, first_ev.id)
        elif p.change == "new" and p.count >= 5:
            add("new_pattern", first_ev.ts, p.service, f"New log pattern in {p.service}",
                f"\"{p.template[:90]}\" appeared {p.count} times; it never appeared in the baseline", 0.35, [p.first_event_id])

    spike_starts: list[datetime] = []
    for svc, series in metrics.items():
        for kind in ("error", "latency"):
            for t, peak, z in spikes(series, baseline_end, kind)[:1]:
                in_min = [e for e in logs if e.service == svc and minute(e.ts) == t]
                pick = next((e for e in in_min if e.severity in ("error", "critical")), in_min[0] if in_min else None)
                if not pick:
                    continue
                label = f"{peak:.0%} errors" if kind == "error" else f"p95 {peak:.0f} ms"
                add(f"{kind}_spike", t, svc, f"{'Error' if kind == 'error' else 'Latency'} spike in {svc}",
                    f"{label} per minute, z-score {z:.1f} against the baseline", min(1.0, 0.4 + z / 40), [pick.id])
                if kind == "error":
                    spike_starts.append(t)
                # Exact error timestamps beat minute-level spikes when ordering services.
                if svc not in service_first:
                    service_first[svc] = (pick.ts, pick.id)

    alerts = [e for e in events if e.source == "alert"]
    for a in alerts:
        fired = "firing" in a.tags
        add("alert_fired" if fired else "alert_resolved", a.ts, a.service,
            f"Alert {'fired' if fired else 'resolved'}: {a.tags[1] if len(a.tags) > 1 else 'alert'}",
            a.message, 0.8 if fired else 0.5, [a.id])

    changes = [e for e in events if e.source == "deploy"]

    # 5. Incident timing.
    first_err_ts = min((by_id[p.first_event_id].ts for p in error_patterns), default=None)
    candidates = [t for t in [first_err_ts, min(spike_starts, default=None)] if t]
    start = min(candidates) if candidates else None
    fired = [a for a in alerts if "firing" in a.tags and (start is None or a.ts >= start - timedelta(minutes=5))]
    humans = [e for e in events if e.source == "chat" and "human" in e.tags and (start is None or e.ts >= start - timedelta(minutes=2))]
    detection = min([a.ts for a in fired[:1]] + [h.ts for h in humans[:1]], default=None)
    if humans:
        h = humans[0]
        add("first_human", h.ts, "", "First message from a person",
            f"{h.raw.get('role') or 'someone'}: \"{h.message[:90]}\"", 0.6, [h.id])

    mitigation, mit_ids = None, []
    if detection:
        mit_changes = [c for c in changes if c.ts >= detection and ("rollback" in c.tags or "config" in c.tags or "restart" in c.tags)]
        mit_chat = [h for h in humans if h.ts >= detection and MITIGATION_WORDS.search(h.message)]
        options = [(c.ts, c.id) for c in mit_changes[:1]] + [(h.ts, h.id) for h in mit_chat[:1]]
        if options:
            mitigation, first_id = min(options)
            mit_ids = [i for _, i in sorted(options)]
            add("mitigation", mitigation, by_id[first_id].service, "Mitigation applied",
                by_id[first_id].message[:120], 0.9, mit_ids)

    recovery = None
    if start:
        recovery = recovered_at(metrics, baseline_end, mitigation or start)
        if recovery:
            near = [a for a in alerts if "resolved" in a.tags and abs((a.ts - recovery).total_seconds()) < 600]
            ok_line = next((e for e in logs if e.ts >= recovery and e.severity == "info"), None)
            ids = [a.id for a in near[:2]] or ([ok_line.id] if ok_line else [])
            add("recovery", recovery, "", "Back to normal",
                "Error rates of all services stayed near their baseline for 3 minutes in a row", 0.9, ids)

    for c in changes:
        head, _, desc = c.message.partition(": ")
        add("change", c.ts, c.service, head, desc or head, 0.5, [c.id])

    # 6. Who failed first, and which change most likely triggered it.
    root, order = first_failing(service_first)
    errors_sample = [by_id[p.first_event_id] for p in error_patterns]
    triggers = rank_triggers(changes, start or ws, root, set(service_first), items, errors_sample) if start else []
    top = {t.event_id for t in triggers[:1] if t.event_id}
    for m in moments:
        if m.kind == "change" and set(m.event_ids) & top:
            m.score = 1.0
            m.reason = "Top trigger candidate. " + m.reason

    moments.sort(key=lambda m: m.ts)
    for i, m in enumerate(moments, 1):
        m.id = f"k{i}"
    shown_patterns = [p for p in items if p.change in ("new", "increased")]
    return IncidentAnalysis(
        incident_id=incident_id, window_start=ws, window_end=we, baseline_end=baseline_end,
        start=start, detection=detection, mitigation=mitigation, recovery=recovery,
        time_to_detect_min=_mins(start, detection), time_to_mitigate_min=_mins(start, mitigation),
        time_to_recover_min=_mins(start, recovery),
        first_failing_service=root, service_order=order, key_moments=moments, patterns=items, triggers=triggers,
        metrics=metrics, log_lines=len(logs), timeline_items=len(moments) + len(shown_patterns),
        counts={s: sum(1 for e in events if e.source == s) for s in ("log", "alert", "deploy", "chat")},
    )
