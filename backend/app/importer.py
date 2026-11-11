"""Import a whole incident bundle: a folder or zip with logs, alerts, deploys and chat."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from .ingest import INGESTERS
from .models import IngestResult
from .store import Store


def classify(name: str) -> str | None:
    n = name.lower().replace("\\", "/")
    base = n.rsplit("/", 1)[-1]
    if "/chat/" in f"/{n}" or n.startswith("chat/") or "slack" in n:
        return "chat"
    if base.endswith((".log", ".jsonl", ".txt")) or base.startswith("log"):
        return "log"
    if base.startswith("alert"):
        return "alert"
    if base.startswith(("deploy", "change")):
        return "deploy"
    return None


def import_bundle(store: Store, incident_id: str, data: bytes | Path) -> list[IngestResult]:
    files: dict[str, bytes] = {}
    if isinstance(data, Path):
        files = {str(p.relative_to(data)).replace("\\", "/"): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            files = {n: z.read(n) for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n}
    results: list[IngestResult] = []
    chat_files: dict[str, bytes] = {}
    for name, blob in sorted(files.items()):
        kind = classify(name)
        if kind == "chat":
            rel = name.split("chat/", 1)[1] if "chat/" in name else name
            chat_files[rel] = blob
        elif kind in ("log", "alert", "deploy"):
            batch = INGESTERS[kind].parse(blob, incident_id, name=name)
            store.add_events(batch.events)
            results.append(batch.result())
    if chat_files:
        batch = INGESTERS["chat"].parse(chat_files, incident_id, name="chat")
        store.add_events(batch.events)
        results.append(batch.result())
    return results
