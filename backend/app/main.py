"""BlackBox API. Run with:  python -m uvicorn app.main:app --app-dir backend --port 8001"""
from __future__ import annotations

import logging
import mimetypes
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .config import ROOT, settings
from .importer import import_bundle
from .ingest import INGESTERS
from .llm import default_client
from .models import Annotation, Event, Incident, IncidentAnalysis, IngestResult, Postmortem
from .pipeline import analyze
from .postmortem import build_postmortem, to_markdown
from .store import Store
from .timeutil import to_utc

sys.path.insert(0, str(ROOT / "demo"))
from simulate import SCENARIOS, simulate  # noqa: E402

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("drain3").setLevel(logging.WARNING)
log = logging.getLogger("blackbox")

store = Store(settings.db_path)
app = FastAPI(title="BlackBox", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
                   allow_methods=["*"], allow_headers=["*"])
DIST = ROOT / "frontend" / "dist"


def _incident(inc_id: str) -> Incident:
    inc = store.get_incident(inc_id)
    if not inc:
        raise HTTPException(404, "Incident not found.")
    return inc


def _analysis(inc_id: str) -> IncidentAnalysis:
    _incident(inc_id)
    cached = store.cached(inc_id, "analysis")
    if cached:
        return IncidentAnalysis.model_validate_json(cached)
    try:
        a = analyze(store, inc_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    store.cache(inc_id, "analysis", a.model_dump_json())
    return a


def _target(incident_id: str | None) -> str:
    """Use the given incident, else the open one, else open a new one."""
    if incident_id:
        return _incident(incident_id).id
    inc = store.open_incident() or store.create_incident(f"Incident {datetime.now():%Y-%m-%d %H:%M}")
    return inc.id


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "llm_enabled": settings.llm_enabled, "scenarios": SCENARIOS}


# ---------------- incidents ----------------
class NewIncident(BaseModel):
    title: str


class DemoRun(BaseModel):
    scenario: str = "bad_deploy"
    seed: int = 1


@app.get("/api/incidents", response_model=list[Incident])
def list_incidents() -> list[Incident]:
    return store.list_incidents()


@app.post("/api/incidents", response_model=Incident)
def create_incident(body: NewIncident) -> Incident:
    return store.create_incident(body.title.strip() or "Untitled incident")


@app.post("/api/demo/run", response_model=Incident)
def run_demo(body: DemoRun) -> Incident:
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario. Choose one of: {', '.join(SCENARIOS)}")
    with tempfile.TemporaryDirectory() as tmp:
        truth = simulate(body.scenario, body.seed, Path(tmp))
        title = f"{body.scenario.replace('_', ' ').capitalize()} (chaos run #{body.seed})"
        inc = store.create_incident(title, scenario=body.scenario, ground_truth=truth)
        results = import_bundle(store, inc.id, Path(tmp))
    log.info("demo incident %s: %s", inc.id, [(r.source, r.accepted, r.bad_lines) for r in results])
    _analysis(inc.id)
    store.set_status(inc.id, "resolved")
    return _incident(inc.id)


@app.post("/api/incidents/{inc_id}/import", response_model=list[IngestResult])
async def import_zip(inc_id: str, file: UploadFile = File(...)) -> list[IngestResult]:
    _incident(inc_id)
    data = await file.read()
    try:
        return await run_in_threadpool(import_bundle, store, inc_id, data)
    except Exception as exc:
        raise HTTPException(400, f"Could not read the bundle: {exc}") from exc


@app.get("/api/incidents/{inc_id}", response_model=Incident)
def get_incident(inc_id: str) -> Incident:
    return _incident(inc_id)


@app.delete("/api/incidents/{inc_id}")
def delete_incident(inc_id: str) -> dict:
    return {"deleted": store.delete_incident(inc_id)}


@app.post("/api/incidents/{inc_id}/resolve", response_model=Incident)
def resolve(inc_id: str) -> Incident:
    _incident(inc_id)
    store.set_status(inc_id, "resolved")
    return _incident(inc_id)


@app.get("/api/incidents/{inc_id}/analysis", response_model=IncidentAnalysis)
async def get_analysis(inc_id: str) -> IncidentAnalysis:
    return await run_in_threadpool(_analysis, inc_id)


@app.get("/api/incidents/{inc_id}/ground-truth")
def ground_truth(inc_id: str) -> dict:
    _incident(inc_id)
    return store.ground_truth(inc_id) or {}


@app.get("/api/incidents/{inc_id}/events", response_model=list[Event])
def events(inc_id: str, source: str | None = None, service: str | None = None, severity: str | None = None,
           q: str | None = None, start: str | None = None, end: str | None = None,
           pattern: str | None = None, ids: str | None = None, limit: int = Query(200, le=2000),
           offset: int = 0) -> list[Event]:
    _incident(inc_id)
    rows = store.query(inc_id, start=to_utc(start) if start else None, end=to_utc(end) if end else None,
                       source=source, service=service, min_severity=severity, text=q,
                       ids=[i for i in ids.split(",") if i] if ids else None,
                       limit=None if pattern else limit, offset=0 if pattern else offset)
    if pattern:
        rows = [e for e in rows if e.pattern_id == pattern][offset:offset + limit]
    return rows


@app.get("/api/incidents/{inc_id}/annotations", response_model=dict[str, Annotation])
def annotations(inc_id: str) -> dict[str, Annotation]:
    return store.annotations(inc_id)


@app.post("/api/incidents/{inc_id}/annotations", response_model=Annotation)
def annotate(inc_id: str, body: Annotation) -> Annotation:
    _incident(inc_id)
    if not store.existing_ids([body.event_id]):
        raise HTTPException(404, "Event not found.")
    store.annotate(inc_id, body)
    return body


@app.post("/api/incidents/{inc_id}/postmortem", response_model=Postmortem)
async def make_postmortem(inc_id: str) -> Postmortem:
    a = await run_in_threadpool(_analysis, inc_id)
    pm = await run_in_threadpool(build_postmortem, store, a, _incident(inc_id).title, default_client())
    store.cache(inc_id, "postmortem", pm.model_dump_json())
    return pm


@app.get("/api/incidents/{inc_id}/postmortem", response_model=Postmortem)
async def get_postmortem(inc_id: str) -> Postmortem:
    cached = store.cached(inc_id, "postmortem")
    if cached:
        return Postmortem.model_validate_json(cached)
    return await make_postmortem(inc_id)


@app.get("/api/incidents/{inc_id}/postmortem.md", response_class=PlainTextResponse)
async def postmortem_md(inc_id: str) -> PlainTextResponse:
    pm = await get_postmortem(inc_id)
    return PlainTextResponse(to_markdown(pm), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="postmortem-{inc_id}.md"'})


# ---------------- ingestion ----------------
def _ingest(source: str, data: bytes, inc_id: str, name: str = "", **kw) -> IngestResult:
    batch = INGESTERS[source].parse(data, inc_id, name=name, **kw) if kw else INGESTERS[source].parse(data, inc_id, name=name)
    store.add_events(batch.events)
    return batch.result()


@app.post("/api/ingest/logs", response_model=IngestResult)
async def ingest_logs(request: Request, incident_id: str | None = None, file: UploadFile | None = File(None)) -> IngestResult:
    """Upload a log file, or POST JSON lines / plain text as the request body."""
    inc = _target(incident_id)
    data, name = (await file.read(), file.filename or "") if file else (await request.body(), "http")
    return await run_in_threadpool(_ingest, "log", data, inc, name)


@app.post("/api/ingest/alerts", response_model=IngestResult)
async def ingest_alerts(request: Request, incident_id: str | None = None) -> IngestResult:
    """Point Alertmanager's webhook receiver here. A firing alert opens an incident if none is open."""
    return _ingest("alert", await request.body(), _target(incident_id), "alertmanager")


@app.post("/api/ingest/deploys", response_model=IngestResult)
async def ingest_deploys(request: Request, incident_id: str | None = None,
                         x_github_event: str | None = Header(None)) -> IngestResult:
    """Manual JSON (one object or a list) or a GitHub webhook (deployment_status, push)."""
    return _ingest("deploy", await request.body(), _target(incident_id), "deploys", github_event=x_github_event)


@app.post("/api/ingest/chat", response_model=IngestResult)
async def ingest_chat(incident_id: str | None = None, file: UploadFile = File(...)) -> IngestResult:
    """Upload a Slack export as a .zip."""
    return await run_in_threadpool(_ingest, "chat", await file.read(), _target(incident_id), file.filename or "chat.zip")


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (DIST / path).resolve()
        if path and target.is_file() and DIST.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
