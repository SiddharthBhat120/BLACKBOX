"""Phase 8: a blameless postmortem draft where every sentence carries the event ids it relies on.

Input is only the key moments, trigger candidates and pinned events (never raw logs). After
drafting (template or LLM), the code checks that each cited id exists in this incident.
Sentences without valid evidence are kept but marked "unverified" so a reader can see them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError

from .llm import LLMClient, parse_json
from .models import Event, IncidentAnalysis, PMSentence, Postmortem
from .store import Store

SECTIONS = ["summary", "impact", "timeline", "trigger_candidates", "went_well", "went_badly", "follow_ups"]
TITLES = {"summary": "Summary", "impact": "Impact", "timeline": "Timeline", "trigger_candidates": "Trigger candidates",
          "went_well": "What went well", "went_badly": "What went badly", "follow_ups": "Follow-up actions"}


def _t(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%H:%M UTC") if dt else "unknown"


def people_names(store: Store, inc_id: str) -> set[str]:
    names = set()
    for e in store.query(inc_id, source="chat"):
        if e.raw.get("user") and not e.raw.get("is_bot"):
            names.add(str(e.raw["user"]))
            names.add(str(e.raw["user"]).split()[0])
    return {n for n in names if len(n) > 2}


def blameless(text: str, names: set[str]) -> str:
    """Talk about systems and roles, not people."""
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(n)}\b", "an engineer", text)
    return re.sub(r"\b(an engineer)('s)?\s+(mistake|fault|error)\b", "the change", text, flags=re.I)


def template_draft(a: IncidentAnalysis, pinned: list[Event], notes: dict[str, str]) -> dict[str, list[PMSentence]]:
    S = {k: [] for k in SECTIONS}
    first = {m.kind: m for m in reversed(sorted(a.key_moments, key=lambda m: m.ts))}
    root = a.first_failing_service or "an unknown service"
    peak = {svc: max((p.error_rate for p in s), default=0) for svc, s in a.metrics.items()}
    spike = first.get("error_spike")
    err = first.get("first_error")
    rec = first.get("recovery")

    S["summary"].append(PMSentence(
        text=f"From {_t(a.start)} to {_t(a.recovery)}, {root} began failing and the failure spread to "
             f"{', '.join(s for s, _ in a.service_order[1:]) or 'no other services'}.",
        evidence=[i for m in (err, rec) if m for i in m.event_ids]))
    top = a.triggers[0] if a.triggers else None
    if top and top.event_id:
        S["summary"].append(PMSentence(
            text=f"The leading trigger candidate is \"{top.title}\", {top.minutes_before_start} minutes before the first errors. "
                 f"This is a correlation, confirmed only if reverting it stopped the errors.", evidence=top.evidence_ids))
    elif top:
        S["summary"].append(PMSentence(text="No deploy or config change explains the timing; an outside dependency is the main lead.",
                                       evidence=top.evidence_ids))

    for svc, rate in sorted(peak.items(), key=lambda x: -x[1]):
        if rate >= 0.02:
            m = next((x for x in a.key_moments if x.kind == "error_spike" and x.service == svc), None)
            S["impact"].append(PMSentence(text=f"{svc} reached a peak error rate of {rate:.0%} per minute.",
                                          evidence=m.event_ids if m else []))
    if a.time_to_recover_min is not None:
        S["impact"].append(PMSentence(text=f"Users were affected for about {a.time_to_recover_min:.0f} minutes.",
                                      evidence=[i for m in (err or spike, rec) if m for i in m.event_ids]))

    for m in sorted(a.key_moments, key=lambda m: m.ts):
        if m.kind == "change" and m.score < 1.0:
            continue
        S["timeline"].append(PMSentence(text=f"{_t(m.ts)}: {m.title}. {m.reason}", evidence=m.event_ids))
    for e in pinned:
        note = notes.get(e.id, "")
        S["timeline"].append(PMSentence(text=f"{_t(e.ts)}: pinned by the team: {e.message[:120]}" + (f" ({note})" if note else ""),
                                        evidence=[e.id]))

    for t in a.triggers[:3]:
        S["trigger_candidates"].append(PMSentence(
            text=f"Candidate {t.rank} (score {t.score}): {t.title}. " + "; ".join(t.evidence) + ".", evidence=t.evidence_ids))

    fired = first.get("alert_fired")
    if fired and a.time_to_detect_min is not None:
        S["went_well"].append(PMSentence(text=f"An alert detected the problem {a.time_to_detect_min:.0f} minutes after the first errors.",
                                         evidence=fired.event_ids))
    mit = first.get("mitigation")
    if mit and a.time_to_mitigate_min is not None:
        S["went_well"].append(PMSentence(text=f"Mitigation started {a.time_to_mitigate_min:.0f} minutes after the first errors: {mit.reason[:100]}.",
                                         evidence=mit.event_ids))
    if a.time_to_detect_min and a.time_to_detect_min >= 2 and err:
        S["went_badly"].append(PMSentence(text=f"Errors ran for {a.time_to_detect_min:.0f} minutes before anyone was alerted.",
                                          evidence=err.event_ids))
    if top and top.event_id and "config" in top.title.lower():
        S["went_badly"].append(PMSentence(text="A configuration change reached production without a check that would have caught the bad value.",
                                          evidence=[top.event_id]))
    elif top and top.event_id:
        S["went_badly"].append(PMSentence(text=f"The {top.service} release went to all traffic at once, so every user met the failure.",
                                          evidence=[top.event_id]))
    newp = first.get("new_pattern")
    if newp:
        S["went_badly"].append(PMSentence(text=f"A new log pattern appeared in {newp.service} before the failure, and nothing alerted on it.",
                                          evidence=newp.event_ids))

    samples = " ".join(m.reason.lower() for m in a.key_moments if m.kind == "first_error")
    if top and top.event_id and "config" in top.title.lower():
        S["follow_ups"].append(PMSentence(text=f"Validate {top.service} config values in CI and roll config out gradually.", evidence=[top.event_id]))
    elif top and top.event_id:
        S["follow_ups"].append(PMSentence(text=f"Ship {top.service} releases as a canary with automatic rollback on error-rate alerts.", evidence=[top.event_id]))
    else:
        S["follow_ups"].append(PMSentence(text=f"Add a circuit breaker and a backup path for {root}'s outside dependency.", evidence=top.evidence_ids if top else []))
    if "no space left" in samples:
        S["follow_ups"].append(PMSentence(text="Alert on disk usage above 80% and cap DEBUG logging in production.", evidence=err.event_ids if err else []))
    if "db connection" in samples or "pool" in samples:
        S["follow_ups"].append(PMSentence(text="Alert on database pool saturation before requests start failing.", evidence=err.event_ids if err else []))
    if a.time_to_detect_min and a.time_to_detect_min >= 2:
        S["follow_ups"].append(PMSentence(text="Tighten the error-rate alert so it fires within one minute.", evidence=fired.event_ids if fired else []))
    return S


class _LLMSentence(BaseModel):
    text: str
    evidence: list[str] = []


class _LLMDraft(BaseModel):
    sections: dict[str, list[_LLMSentence]]


SYSTEM = """You write blameless incident postmortems. Talk about systems and changes, never about
individual people. Use ONLY the facts given. Every sentence must list the event ids that support it
in "evidence" (ids are given in square brackets). Trigger candidates are correlations, not proof.
Return ONLY JSON: {"sections": {"summary": [{"text": "...", "evidence": ["..."]}], "impact": [...],
"timeline": [...], "trigger_candidates": [...], "went_well": [...], "went_badly": [...], "follow_ups": [...]}}"""


def llm_draft(a: IncidentAnalysis, pinned: list[Event], notes: dict[str, str], client: LLMClient) -> dict[str, list[PMSentence]] | None:
    facts = {
        "start": _t(a.start), "detected": _t(a.detection), "mitigated": _t(a.mitigation), "recovered": _t(a.recovery),
        "first_failing_service": a.first_failing_service,
        "key_moments": [f"[{','.join(m.event_ids)}] {_t(m.ts)} {m.kind} {m.service}: {m.title}. {m.reason}" for m in a.key_moments],
        "trigger_candidates": [f"[{','.join(t.evidence_ids)}] rank {t.rank} score {t.score}: {t.title}; {'; '.join(t.evidence)}" for t in a.triggers[:3]],
        "pinned_by_team": [f"[{e.id}] {_t(e.ts)} {e.message[:160]} {notes.get(e.id, '')}" for e in pinned],
    }
    try:
        draft = _LLMDraft(**parse_json(client.complete(SYSTEM, json.dumps(facts, indent=1))))
    except (ValidationError, ValueError, KeyError, Exception):
        return None
    return {k: [PMSentence(text=s.text, evidence=s.evidence) for s in draft.sections.get(k, [])] for k in SECTIONS}


def build_postmortem(store: Store, a: IncidentAnalysis, title: str, client: LLMClient | None = None) -> Postmortem:
    ann = store.annotations(a.incident_id)
    pinned_ids = [eid for eid, x in ann.items() if x.pinned]
    pinned = store.query(a.incident_id, ids=pinned_ids) if pinned_ids else []
    notes = {eid: x.note for eid, x in ann.items() if x.note}
    sections, engine = None, "template"
    if client is not None:
        sections = llm_draft(a, pinned, notes, client)
        engine = "llm" if sections else "template"
    if sections is None:
        sections = template_draft(a, pinned, notes)

    cited = sorted({i for sents in sections.values() for s in sents for i in s.evidence})
    valid = store.existing_ids(cited)
    names = people_names(store, a.incident_id)
    unverified = 0
    for sents in sections.values():
        for s in sents:
            s.evidence = [i for i in s.evidence if i in valid]
            s.verified = bool(s.evidence)
            unverified += 0 if s.verified else 1
            s.text = blameless(s.text, names)
    return Postmortem(incident_id=a.incident_id, title=title, engine=engine, sections=sections,
                      unverified=unverified, generated_at=datetime.now(timezone.utc))


def to_markdown(pm: Postmortem) -> str:
    lines = [f"# Postmortem: {pm.title}", "",
             f"_Draft generated {pm.generated_at:%Y-%m-%d %H:%M} UTC by BlackBox ({pm.engine}). Blameless: it describes systems, not people. "
             f"Trigger candidates are correlations, not proof._", ""]
    for key in SECTIONS:
        sents = pm.sections.get(key, [])
        if not sents:
            continue
        lines += [f"## {TITLES[key]}", ""]
        for s in sents:
            ev = " ".join(f"`{i}`" for i in s.evidence)
            mark = "" if s.verified else " **(unverified: no evidence)**"
            lines.append(f"- {s.text}{mark}" + (f" <sub>evidence: {ev}</sub>" if ev else ""))
        lines.append("")
    return "\n".join(lines)
