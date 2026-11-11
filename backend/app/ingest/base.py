from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import Event, IngestResult, Source

MAX_ERRORS_SHOWN = 5


def event_id(source: Source, incident_id: str, *parts: Any) -> str:
    """Stable id: ingesting the same file twice does not create duplicates."""
    h = hashlib.sha1("|".join(str(p) for p in (incident_id, source, *parts)).encode("utf-8")).hexdigest()
    return f"{source[0]}{h[:13]}"


@dataclass
class Batch:
    source: Source
    events: list[Event] = field(default_factory=list)
    bad: int = 0
    errors: list[str] = field(default_factory=list)

    def fail(self, where: str, why: str) -> None:
        self.bad += 1
        if len(self.errors) < MAX_ERRORS_SHOWN:
            self.errors.append(f"{where}: {why}")

    def result(self) -> IngestResult:
        return IngestResult(source=self.source, accepted=len(self.events), bad_lines=self.bad, errors=self.errors)


class Ingester(Protocol):
    source: Source

    def parse(self, data: Any, incident_id: str, name: str = "") -> Batch: ...
