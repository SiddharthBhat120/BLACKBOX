"""Per-service, per-minute error rate and p95 latency, derived from the logs."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .models import Event, MetricPoint

LATENCY = re.compile(r"\b(?:in|took|after)\s+(\d+(?:\.\d+)?)\s?ms\b")


def minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def per_minute(events: list[Event], start: datetime, end: datetime) -> dict[str, list[MetricPoint]]:
    buckets: dict[tuple[str, datetime], list] = defaultdict(lambda: [0, 0, []])
    services: set[str] = set()
    for e in events:
        if e.source != "log" or not e.service:
            continue
        services.add(e.service)
        b = buckets[(e.service, minute(e.ts))]
        b[0] += 1
        if e.severity in ("error", "critical"):
            b[1] += 1
        m = LATENCY.search(e.message)
        if m:
            b[2].append(float(m.group(1)))
    out: dict[str, list[MetricPoint]] = {}
    for svc in sorted(services):
        series = []
        t = minute(start)
        while t <= end:
            n, err, lats = buckets.get((svc, t), (0, 0, []))
            lats = sorted(lats)
            p95 = lats[int(0.95 * (len(lats) - 1))] if lats else None
            series.append(MetricPoint(t=t, requests=n, errors=err, error_rate=round(err / n, 4) if n else 0.0, p95_ms=p95))
            t += timedelta(minutes=1)
        out[svc] = series
    return out


def baseline_stats(values: list[float], floor: float) -> tuple[float, float]:
    if not values:
        return 0.0, floor
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    return mean, max(math.sqrt(var), floor)


def spikes(series: list[MetricPoint], baseline_end: datetime, kind: str, sustain: int = 2) -> list[tuple[datetime, float, float]]:
    """Return runs where the metric is far above baseline: [(start minute, peak value, peak z)]."""
    base = [p for p in series if p.t < baseline_end]
    if kind == "error":
        vals = [p.error_rate for p in base]
        mean, std = baseline_stats(vals, 0.005)
        get = lambda p: p.error_rate  # noqa: E731
        ok = lambda v: v >= 0.02  # noqa: E731
    else:
        vals = [p.p95_ms for p in base if p.p95_ms is not None]
        mean, std = baseline_stats(vals, 1.0)
        std = max(std, 0.1 * mean)
        get = lambda p: p.p95_ms or 0.0  # noqa: E731
        ok = lambda v: v >= 1.5 * mean and v - mean >= 100  # noqa: E731
    runs, cur = [], []
    for p in series:
        v = get(p)
        z = (v - mean) / std
        if z > 4 and ok(v):
            cur.append((p.t, v, z))
        else:
            if len(cur) >= sustain:
                runs.append(cur)
            cur = []
    if len(cur) >= sustain:
        runs.append(cur)
    return [(r[0][0], max(x[1] for x in r), max(x[2] for x in r)) for r in runs]


def recovered_at(metrics: dict[str, list[MetricPoint]], baseline_end: datetime, after: datetime,
                 run: int = 3) -> datetime | None:
    """First minute after `after` from which every service stays near its baseline error rate for `run` minutes."""
    limits: dict[str, float] = {}
    for svc, series in metrics.items():
        mean, std = baseline_stats([p.error_rate for p in series if p.t < baseline_end], 0.005)
        limits[svc] = max(mean + 3 * std, 0.02)
    times = [p.t for p in next(iter(metrics.values()))] if metrics else []
    idx = {svc: {p.t: p for p in s} for svc, s in metrics.items()}
    streak: list[datetime] = []
    for t in times:
        if t <= minute(after):
            continue
        good = all((idx[s].get(t) is None or idx[s][t].requests == 0 or idx[s][t].error_rate <= limits[s]) for s in metrics)
        streak = streak + [t] if good else []
        if len(streak) >= run:
            return streak[0]
    return None


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)
