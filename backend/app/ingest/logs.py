"""Logs: JSON lines or common plain-text formats."""
from __future__ import annotations

import json
import re
from pathlib import PurePath
from typing import Any

from ..models import Event, Severity
from ..timeutil import to_utc
from .base import Batch, event_id

LEVELS: dict[str, Severity] = {
    "trace": "debug", "debug": "debug", "info": "info", "information": "info", "notice": "info",
    "warn": "warning", "warning": "warning", "err": "error", "error": "error",
    "crit": "critical", "critical": "critical", "fatal": "critical", "emerg": "critical", "alert": "critical",
}
TS = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
PLAIN = [
    # 2026-11-10T09:00:01.123Z ERROR orders message      |  2026-11-10 09:00:01,123 WARN [orders] message
    re.compile(rf"^(?P<ts>{TS})\s+(?P<level>[A-Za-z]+)\s+\[?(?P<service>[\w.\-]+)\]?:?\s+(?P<msg>.+)$"),
    # [2026-11-10 09:00:01,123] WARNING [orders] message
    re.compile(rf"^\[(?P<ts>{TS})\]\s+(?P<level>[A-Za-z]+)\s+\[(?P<service>[\w.\-]+)\]\s*(?P<msg>.+)$"),
    # Python logging: 2026-11-10 09:00:01,123 - orders - ERROR - message
    re.compile(rf"^(?P<ts>{TS})\s+-\s+(?P<service>[\w.\-]+)\s+-\s+(?P<level>[A-Za-z]+)\s+-\s+(?P<msg>.+)$"),
]
TS_KEYS = ("ts", "timestamp", "time", "@timestamp", "datetime", "date")
LEVEL_KEYS = ("level", "severity", "levelname", "log.level", "lvl")
SERVICE_KEYS = ("service", "service.name", "app", "application", "logger", "component")
MSG_KEYS = ("msg", "message", "event", "log", "text")


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def norm_level(raw: Any) -> Severity:
    return LEVELS.get(str(raw or "info").strip().lower(), "info")


class LogIngester:
    source = "log"

    def parse(self, data: Any, incident_id: str, name: str = "") -> Batch:
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
        default_service = PurePath(name).stem.split(".")[0] if name else ""
        batch = Batch("log")
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                if line.startswith("{"):
                    rec = json.loads(line)
                    if not isinstance(rec, dict):
                        raise ValueError("not a JSON object")
                    ts_raw, msg = _first(rec, TS_KEYS), _first(rec, MSG_KEYS)
                    if ts_raw is None or msg is None:
                        raise ValueError("missing timestamp or message")
                    level, service, raw = _first(rec, LEVEL_KEYS), _first(rec, SERVICE_KEYS) or default_service, rec
                else:
                    m = next((p.match(line) for p in PLAIN if p.match(line)), None)
                    if not m:
                        raise ValueError("unknown log format")
                    ts_raw, level, service, msg = m["ts"], m["level"], m["service"], m["msg"]
                    raw = {"line": line}
                ts = to_utc(ts_raw)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                batch.fail(f"{name or 'logs'} line {n}", str(exc)[:80])
                continue
            batch.events.append(Event(
                id=event_id("log", incident_id, name, n, line), ts=ts, source="log", service=str(service),
                severity=norm_level(level), message=str(msg), raw=raw, incident_id=incident_id,
            ))
        return batch
