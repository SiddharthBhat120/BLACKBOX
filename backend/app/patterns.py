"""Phase 4: log template mining with Drain, and noise reduction.

Drain in simple words: it reads log lines one by one and groups lines that have the same
"shape". It first splits by length (number of words), then walks a small tree keyed by the
first few words, and finally compares the line with the existing groups word by word. If
enough words match, the line joins that group and the words that differ become <*>.
So "fetched order o-123 in 31ms" and "fetched order o-987 in 29ms" become one template:
"fetched order <ID> in <NUM>ms". Thousands of lines turn into a few dozen templates.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from .models import Event, PatternItem
from .store import SEVERITIES

logging.getLogger("drain3").setLevel(logging.WARNING)

MASKS = [
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "UUID"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "IP"),
    (r"(?<![A-Za-z0-9])[a-z]{1,3}-[0-9a-f]{3,}\b", "ID"),       # o-41001, u-532, s-1a2b
    (r"\bs[0-9a-f]{6,}\b", "ID"),
    (r"\b0x[0-9a-fA-F]+\b", "HEX"),
    (r"\b[0-9a-f]{7,40}\b", "HEX"),
    (r"(?<![A-Za-z])-?\d+(?:\.\d+)?(?=ms|s|%|\b)", "NUM"),
]


def _miner() -> TemplateMiner:
    cfg = TemplateMinerConfig()
    cfg.drain_sim_th = 0.5
    cfg.drain_depth = 4
    cfg.drain_max_children = 100
    cfg.masking_instructions = [MaskingInstruction(p, m) for p, m in MASKS]
    cfg.mask_prefix, cfg.mask_suffix = "<", ">"
    cfg.parametrize_numeric_tokens = True
    return TemplateMiner(config=cfg)


def mine(events: list[Event]) -> dict[str, tuple[str, str]]:
    """Assign a pattern to every log event. Returns {event_id: (pattern_id, template)}.

    One miner per service, so identical wording in two services stays two patterns.
    """
    miners: dict[str, TemplateMiner] = {}
    assigned: dict[str, tuple[str, int]] = {}
    for e in events:
        if e.source != "log":
            continue
        m = miners.setdefault(e.service, _miner())
        res = m.add_log_message(e.message)
        assigned[e.id] = (e.service, res["cluster_id"])
    templates = {(svc, c.cluster_id): c.get_template() for svc, m in miners.items() for c in m.drain.clusters}
    return {eid: (f"{svc}#{cid}", templates[(svc, cid)]) for eid, (svc, cid) in assigned.items()}


def collapse(events: list[Event], patterns: dict[str, tuple[str, str]], baseline_end: datetime,
             window_start: datetime, window_end: datetime) -> list[PatternItem]:
    """Collapse repeats and compare the incident window with the baseline before it."""
    groups: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.id in patterns:
            groups[patterns[e.id][0]].append(e)
    base_min = max(1.0, (baseline_end - window_start).total_seconds() / 60)
    inc_min = max(1.0, (window_end - baseline_end).total_seconds() / 60)
    items: list[PatternItem] = []
    for pid, evs in groups.items():
        b = sum(1 for e in evs if e.ts < baseline_end)
        i = len(evs) - b
        b_rate, i_rate = b / base_min, i / inc_min
        if b == 0 and i > 0:
            change = "new"
        elif i_rate > 2 * b_rate + 0.2:
            change = "increased"
        elif b >= 5 and i_rate < 0.5 * b_rate:
            change = "decreased"
        else:
            change = "unchanged"
        sev = max((e.severity for e in evs), key=SEVERITIES.index)
        first = evs[0] if change != "new" else next(e for e in evs if e.ts >= baseline_end)
        items.append(PatternItem(
            pattern_id=pid, template=patterns[evs[0].id][1], service=evs[0].service, severity=sev,
            count=len(evs), baseline_count=b, incident_count=i, change=change,
            first=evs[0].ts, last=evs[-1].ts, first_event_id=first.id, sample=evs[0].message,
        ))
    order = {"new": 0, "increased": 1, "decreased": 2, "unchanged": 3}
    items.sort(key=lambda p: (order[p.change], -SEVERITIES.index(p.severity), -p.count))
    return items
