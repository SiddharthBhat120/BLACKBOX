"""Alerts: Alertmanager webhook payloads (status firing or resolved)."""
from __future__ import annotations

import json
from typing import Any

from ..models import Event, Severity
from ..timeutil import to_utc
from .base import Batch, event_id

SEV: dict[str, Severity] = {"critical": "critical", "page": "critical", "error": "error", "warning": "warning",
                            "warn": "warning", "info": "info", "none": "info"}


class AlertIngester:
    source = "alert"

    def parse(self, data: Any, incident_id: str, name: str = "") -> Batch:
        batch = Batch("alert")
        try:
            payload = json.loads(data) if isinstance(data, (bytes, bytearray, str)) else data
        except json.JSONDecodeError as exc:
            batch.fail(name or "alerts", f"invalid JSON ({exc.msg})")
            return batch
        payloads = payload if isinstance(payload, list) else [payload]
        for p_i, p in enumerate(payloads):
            alerts = p.get("alerts") if isinstance(p, dict) else None
            if not isinstance(alerts, list):
                batch.fail(f"payload {p_i}", "no 'alerts' list")
                continue
            for a in alerts:
                try:
                    labels = a.get("labels", {})
                    status = a.get("status") or p.get("status") or "firing"
                    when = a["endsAt"] if status == "resolved" else a["startsAt"]
                    ts = to_utc(when)
                    alertname = labels.get("alertname", "Alert")
                    service = labels.get("service") or labels.get("job") or labels.get("app") or ""
                    summary = a.get("annotations", {}).get("summary") or a.get("annotations", {}).get("description", "")
                    sev = "info" if status == "resolved" else SEV.get(str(labels.get("severity", "warning")).lower(), "warning")
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    batch.fail(f"payload {p_i}", f"bad alert ({exc})")
                    continue
                batch.events.append(Event(
                    id=event_id("alert", incident_id, a.get("fingerprint", alertname), status, when),
                    ts=ts, source="alert", service=service, severity=sev,
                    message=f"{'Resolved' if status == 'resolved' else 'Firing'}: {alertname} on {service or 'unknown'}"
                            + (f". {summary}" if summary else ""),
                    raw={"status": status, **a}, tags=[status, alertname], incident_id=incident_id,
                ))
        return batch
