"""Pydantic models: the common Event format and everything derived from it."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Source = Literal["log", "alert", "deploy", "chat"]
Severity = Literal["debug", "info", "warning", "error", "critical"]


class Event(BaseModel):
    id: str
    ts: datetime  # always timezone-aware UTC
    source: Source
    service: str = ""
    severity: Severity = "info"
    message: str
    raw: dict[str, Any] = Field(default_factory=dict)
    pattern_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    incident_id: str


class IngestResult(BaseModel):
    source: Source
    accepted: int
    bad_lines: int
    errors: list[str] = Field(default_factory=list)  # first few reasons, for the user


class Incident(BaseModel):
    id: str
    title: str
    created_at: datetime
    status: Literal["open", "resolved"] = "open"
    scenario: str | None = None  # set for chaos-demo incidents
    event_count: int = 0


class Annotation(BaseModel):
    event_id: str
    pinned: bool = False
    hidden: bool = False
    note: str = ""


class PatternItem(BaseModel):
    """Many log lines collapsed into one template."""
    pattern_id: str
    template: str
    service: str
    severity: Severity
    count: int
    baseline_count: int
    incident_count: int
    change: Literal["new", "increased", "decreased", "unchanged"]
    first: datetime
    last: datetime
    first_event_id: str
    sample: str


class KeyMoment(BaseModel):
    id: str
    ts: datetime
    kind: Literal["change", "first_error", "error_spike", "latency_spike", "alert_fired", "alert_resolved",
                  "first_human", "mitigation", "recovery", "new_pattern"]
    service: str
    title: str
    reason: str
    score: float
    event_ids: list[str]


class TriggerCandidate(BaseModel):
    rank: int
    event_id: str | None
    service: str
    title: str
    score: float
    minutes_before_start: float | None
    evidence: list[str]  # human-readable reasons
    evidence_ids: list[str]


class MetricPoint(BaseModel):
    t: datetime
    requests: int
    errors: int
    error_rate: float
    p95_ms: float | None


class IncidentAnalysis(BaseModel):
    incident_id: str
    window_start: datetime
    window_end: datetime
    baseline_end: datetime
    start: datetime | None
    detection: datetime | None
    mitigation: datetime | None
    recovery: datetime | None
    time_to_detect_min: float | None
    time_to_mitigate_min: float | None
    time_to_recover_min: float | None
    first_failing_service: str | None
    service_order: list[tuple[str, datetime]]
    key_moments: list[KeyMoment]
    patterns: list[PatternItem]
    triggers: list[TriggerCandidate]
    metrics: dict[str, list[MetricPoint]]
    log_lines: int
    timeline_items: int
    counts: dict[str, int]


class PMSentence(BaseModel):
    text: str
    evidence: list[str] = Field(default_factory=list)
    verified: bool = True


class Postmortem(BaseModel):
    incident_id: str
    title: str
    engine: Literal["template", "llm"]
    sections: dict[str, list[PMSentence]]
    unverified: int
    generated_at: datetime
