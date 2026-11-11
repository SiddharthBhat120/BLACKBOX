import json

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import FIX

c = TestClient(app)


def test_firing_alert_opens_an_incident_and_ingest_endpoints():
    r = c.post("/api/ingest/alerts", content=(FIX / "alertmanager.json").read_bytes())
    assert r.status_code == 200 and r.json()["accepted"] == 2
    inc = [i for i in c.get("/api/incidents").json() if i["status"] == "open"][0]
    r = c.post(f"/api/ingest/logs?incident_id={inc['id']}", files={"file": ("plain.log", (FIX / "plain.log").read_bytes())})
    assert r.json()["accepted"] == 4 and r.json()["bad_lines"] == 1
    r = c.post(f"/api/ingest/logs?incident_id={inc['id']}", content=(FIX / "app.jsonl").read_bytes())
    assert r.json()["accepted"] == 4
    r = c.post(f"/api/ingest/deploys?incident_id={inc['id']}", content=(FIX / "github_push.json").read_bytes(),
               headers={"X-GitHub-Event": "push"})
    assert r.json()["accepted"] == 1
    events = c.get(f"/api/incidents/{inc['id']}/events?severity=error").json()
    assert all(e["severity"] in ("error", "critical") for e in events)


def test_demo_run_analysis_annotate_postmortem():
    inc = c.post("/api/demo/run", json={"scenario": "config_change", "seed": 3}).json()
    a = c.get(f"/api/incidents/{inc['id']}/analysis").json()
    assert "PROVIDER_TIMEOUT_MS" in a["triggers"][0]["title"]
    ev = a["key_moments"][0]["event_ids"][0]
    assert c.post(f"/api/incidents/{inc['id']}/annotations", json={"event_id": ev, "pinned": True}).status_code == 200
    assert c.get(f"/api/incidents/{inc['id']}/annotations").json()[ev]["pinned"] is True
    pm = c.post(f"/api/incidents/{inc['id']}/postmortem").json()
    assert pm["unverified"] == 0
    md = c.get(f"/api/incidents/{inc['id']}/postmortem.md")
    assert md.status_code == 200 and "Trigger candidates" in md.text
    gt = c.get(f"/api/incidents/{inc['id']}/ground-truth").json()
    assert gt["scenario"] == "config_change"
    assert c.delete(f"/api/incidents/{inc['id']}").json()["deleted"] is True


def test_unknown_scenario_and_empty_incident():
    assert c.post("/api/demo/run", json={"scenario": "nope"}).status_code == 400
    inc = c.post("/api/incidents", json={"title": "empty"}).json()
    assert c.get(f"/api/incidents/{inc['id']}/analysis").status_code == 409
