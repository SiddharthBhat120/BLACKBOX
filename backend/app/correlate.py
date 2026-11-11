"""Phase 6: rank the changes that might have triggered the incident.

Correlation is not proof. A deploy two minutes before the errors is a good lead, but it may
be a coincidence: two things can happen close together while a third (traffic, an outside
provider, a slow memory leak) causes the failure. So the output is "candidates with evidence",
never "the root cause". A human confirms it, for example by rolling back and watching errors stop.

Score = time closeness x service match x visible behaviour change
  time:      exp(-minutes_before_first_error / 15); changes after the start count for little
  service:   1.0 same service that failed first, 0.6 a direct caller/callee, 0.5 another
             affected service, 0.15 unrelated
  behaviour: 1.5 if a new log pattern appears in that service within 10 minutes of the change
             (for services other than the origin, only non-error patterns count: their new
             errors are explained by the failing service they call), 1.0 if that service is
             where errors started, otherwise 0.4 (the change left no trace of its own)
An "outside cause" candidate with a fixed score of 0.2 is always included, so when no change
explains the failure, it rises to the top.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .config import settings
from .models import Event, PatternItem, TriggerCandidate

TAU_MIN = 15.0
LOOKBACK = timedelta(hours=3)
EXTERNAL_SCORE = 0.2


def neighbours() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for pair in settings.dependencies.split(","):
        if ":" in pair:
            a, b = (x.strip() for x in pair.split(":", 1))
            out.setdefault(a, set()).add(b)
            out.setdefault(b, set()).add(a)
    return out


def callee_of(a: str, b: str) -> bool:
    """True if a calls b directly."""
    return any(p.strip() == f"{a}:{b}" for p in settings.dependencies.split(","))


def first_failing(service_first: dict[str, tuple[datetime, str]]) -> tuple[str | None, list[tuple[str, datetime]]]:
    """Order services by their first anomaly. When a caller and its callee fail within
    5 seconds, the callee is the more likely origin (errors travel up the call chain)."""
    order = sorted(((s, t) for s, (t, _) in service_first.items()), key=lambda x: x[1])
    if not order:
        return None, []
    first = order[0][0]
    for svc, t in order[1:]:
        if (t - order[0][1]).total_seconds() <= 5 and callee_of(first, svc):
            first = svc
    return first, order


def rank_triggers(changes: list[Event], start: datetime, root: str | None, affected: set[str],
                  patterns: list[PatternItem], error_samples: list[Event]) -> list[TriggerCandidate]:
    nb = neighbours().get(root or "", set())
    # "New" here means first seen after the change (not "new versus the baseline"): a change can
    # alter behaviour long before the failure, e.g. DEBUG logging that later fills the disk.
    new_by_service: dict[str, list[PatternItem]] = {}
    for p in patterns:
        new_by_service.setdefault(p.service, []).append(p)
    cands: list[TriggerCandidate] = []
    for ch in changes:
        if "rollback" in ch.tags or ch.ts < start - LOOKBACK or ch.ts > start + timedelta(minutes=1):
            continue
        minutes = (start - ch.ts).total_seconds() / 60
        time_w = math.exp(-max(minutes, 0) / TAU_MIN) * (0.3 if minutes < 0 else 1.0)
        if ch.service == root:
            svc_w, svc_why = 1.0, f"{ch.service} is the service where errors started"
        elif ch.service in nb:
            svc_w, svc_why = 0.6, f"{ch.service} calls or is called by {root}, where errors started"
        elif ch.service in affected:
            svc_w, svc_why = 0.5, f"{ch.service} was affected, but errors started in {root}"
        else:
            svc_w, svc_why = 0.15, f"{ch.service} shows no errors in this incident"
        after = [p for p in new_by_service.get(ch.service, [])
                 if ch.ts <= p.first <= ch.ts + timedelta(minutes=10)
                 and (ch.service == root or p.severity not in ("error", "critical"))]
        if after:
            beh_w = 1.5
            beh_why = f"new log pattern in {ch.service} {((after[0].first - ch.ts).total_seconds() / 60):.1f} min later: \"{after[0].template[:70]}\""
        elif ch.service == root:
            beh_w, beh_why = 1.0, "errors began in this service"
        else:
            beh_w, beh_why = 0.4, f"no new behaviour of its own in {ch.service} after this change"
        evidence_ids = [ch.id] + ([after[0].first_event_id] if after else [])
        when = f"{minutes:.1f} min before the first errors" if minutes >= 0 else f"{-minutes:.1f} min after the first errors"
        cands.append(TriggerCandidate(
            rank=0, event_id=ch.id, service=ch.service, title=ch.message, score=round(time_w * svc_w * beh_w, 3),
            minutes_before_start=round(minutes, 1), evidence=[when, svc_why, beh_why], evidence_ids=evidence_ids,
        ))
    hints = [e for e in error_samples if any(w in e.message.lower() for w in ("timeout", "provider", "upstream", "third-party", "unavailable"))]
    cands.append(TriggerCandidate(
        rank=0, event_id=None, service=root or "", title="No recent change explains this: check outside dependencies and traffic",
        score=EXTERNAL_SCORE, minutes_before_start=None,
        evidence=["Used when no deploy or config change scores higher"]
                 + ([f"errors mention an outside call: \"{hints[0].message[:80]}\""] if hints else []),
        evidence_ids=[hints[0].id] if hints else [],
    ))
    cands.sort(key=lambda c: c.score, reverse=True)
    for i, c in enumerate(cands, 1):
        c.rank = i
    return cands
