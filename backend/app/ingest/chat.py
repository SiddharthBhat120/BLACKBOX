"""Chat: a Slack export (zip file or folder with users.json and <channel>/<date>.json files)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from ..models import Event
from ..timeutil import to_utc
from .base import Batch, event_id


def _files_from(data: Any) -> dict[str, bytes]:
    """Return {relative path: bytes} from a zip (bytes), a folder (Path) or a dict."""
    if isinstance(data, dict):
        return {k: (v if isinstance(v, bytes) else json.dumps(v).encode()) for k, v in data.items()}
    if isinstance(data, (str, Path)) and Path(data).is_dir():
        root = Path(data)
        return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes() for p in root.rglob("*.json")}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        out = {}
        for n in z.namelist():
            if n.endswith(".json") and not n.startswith("__MACOSX"):
                out[n] = z.read(n)
        # strip a common top folder ("chat/users.json" -> "users.json")
        tops = {n.split("/")[0] for n in out}
        if len(tops) == 1 and all("/" in n for n in out):
            out = {n.split("/", 1)[1]: v for n, v in out.items()}
        return out


class ChatIngester:
    source = "chat"

    def parse(self, data: Any, incident_id: str, name: str = "") -> Batch:
        batch = Batch("chat")
        try:
            files = _files_from(data)
        except (zipfile.BadZipFile, OSError) as exc:
            batch.fail(name or "chat", f"not a Slack export zip or folder ({exc})")
            return batch
        users: dict[str, dict] = {}
        if "users.json" in files:
            try:
                users = {u["id"]: u for u in json.loads(files["users.json"])}
            except (json.JSONDecodeError, KeyError, TypeError):
                batch.fail("users.json", "could not read users")
        for path, blob in sorted(files.items()):
            if "/" not in path:
                continue  # users.json, channels.json, etc.
            channel = path.split("/")[0]
            try:
                messages = json.loads(blob)
            except json.JSONDecodeError as exc:
                batch.fail(path, f"invalid JSON ({exc.msg})")
                continue
            for i, m in enumerate(messages if isinstance(messages, list) else []):
                try:
                    ts = to_utc(float(m["ts"]))
                    text = str(m.get("text", "")).strip()
                except (KeyError, ValueError, TypeError) as exc:
                    batch.fail(f"{path} message {i}", f"bad message ({exc})")
                    continue
                if not text:
                    continue
                is_bot = m.get("subtype") == "bot_message" or "bot_id" in m
                u = users.get(m.get("user", ""), {})
                who = m.get("username") if is_bot else (u.get("real_name") or u.get("name") or m.get("user", "someone"))
                role = (u.get("profile") or {}).get("title", "")
                batch.events.append(Event(
                    id=event_id("chat", incident_id, channel, m["ts"]), ts=ts, source="chat", service="",
                    severity="info", message=text,
                    raw={**m, "user_id": m.get("user", ""), "channel": channel, "user": who, "role": role, "is_bot": is_bot},
                    tags=["bot" if is_bot else "human", f"#{channel}"], incident_id=incident_id,
                ))
        return batch
