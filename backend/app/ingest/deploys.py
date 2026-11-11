"""Deploys and config changes: a simple manual JSON format, or GitHub webhooks
(deployment_status and push events)."""
from __future__ import annotations

import json
from typing import Any

from ..models import Event
from ..timeutil import to_utc
from .base import Batch, event_id

KINDS = {"deploy", "config", "rollback", "restart", "commit", "feature_flag", "scale"}


class DeployIngester:
    source = "deploy"

    def parse(self, data: Any, incident_id: str, name: str = "", github_event: str | None = None) -> Batch:
        batch = Batch("deploy")
        try:
            payload = json.loads(data) if isinstance(data, (bytes, bytearray, str)) else data
        except json.JSONDecodeError as exc:
            batch.fail(name or "deploys", f"invalid JSON ({exc.msg})")
            return batch
        if isinstance(payload, dict) and ("deployment_status" in payload or github_event == "deployment_status"):
            self._github_deployment(payload, incident_id, batch)
        elif isinstance(payload, dict) and ("commits" in payload or github_event == "push"):
            self._github_push(payload, incident_id, batch)
        else:
            items = payload if isinstance(payload, list) else [payload]
            for i, d in enumerate(items):
                self._manual(d, i, incident_id, batch, name)
        return batch

    def _manual(self, d: Any, i: int, incident_id: str, batch: Batch, name: str) -> None:
        try:
            ts = to_utc(d.get("timestamp") or d["time"])
            service = str(d["service"])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            batch.fail(f"{name or 'deploys'} item {i}", f"needs timestamp and service ({exc})")
            return
        kind = str(d.get("kind", "deploy")).lower()
        kind = kind if kind in KINDS else "deploy"
        version = d.get("version", "")
        desc = d.get("description", "")
        verb = {"config": "Config change", "rollback": "Rollback", "restart": "Restart"}.get(kind, "Deploy")
        batch.events.append(Event(
            id=event_id("deploy", incident_id, ts.isoformat(), service, kind, version),
            ts=ts, source="deploy", service=service, severity="info",
            message=f"{verb} {service} {version}".strip() + (f": {desc}" if desc else ""),
            raw=d, tags=[kind], incident_id=incident_id,
        ))

    def _github_deployment(self, p: dict, incident_id: str, batch: Batch) -> None:
        try:
            st, dep = p["deployment_status"], p["deployment"]
            if st.get("state") not in ("success", "in_progress", "failure", "error"):
                return
            ts = to_utc(st.get("created_at") or dep["created_at"])
            service = p.get("repository", {}).get("name") or dep.get("environment", "app")
        except (KeyError, ValueError, TypeError) as exc:
            batch.fail("github deployment_status", str(exc))
            return
        ref = dep.get("ref") or dep.get("sha", "")[:7]
        batch.events.append(Event(
            id=event_id("deploy", incident_id, "gh", dep.get("id"), st.get("id")), ts=ts, source="deploy",
            service=service, severity="error" if st.get("state") in ("failure", "error") else "info",
            message=f"Deploy {service} {ref} to {dep.get('environment', '')}: {st.get('state')}"
                    + (f" ({dep['description']})" if dep.get("description") else ""),
            raw=p, tags=["deploy", "github"], incident_id=incident_id,
        ))

    def _github_push(self, p: dict, incident_id: str, batch: Batch) -> None:
        service = p.get("repository", {}).get("name", "repo")
        for c in p.get("commits", []):
            try:
                ts = to_utc(c["timestamp"])
            except (KeyError, ValueError) as exc:
                batch.fail("github push", f"commit without timestamp ({exc})")
                continue
            first_line = str(c.get("message", "")).splitlines()[0] if c.get("message") else ""
            batch.events.append(Event(
                id=event_id("deploy", incident_id, "gh-commit", c.get("id")), ts=ts, source="deploy", service=service,
                severity="info", message=f"Commit {str(c.get('id', ''))[:7]} to {service}: {first_line}",
                raw=c, tags=["commit", "github"], incident_id=incident_id,
            ))
