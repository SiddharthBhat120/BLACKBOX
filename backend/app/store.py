"""SQLite storage for events, incidents and annotations.

Timestamps are stored as UTC epoch seconds (REAL) so range queries and sorting are exact,
regardless of the order in which events arrive.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Annotation, Event, Incident

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', scenario TEXT, ground_truth TEXT, analysis TEXT, postmortem TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, ts REAL NOT NULL, source TEXT NOT NULL,
  service TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL, message TEXT NOT NULL,
  raw TEXT NOT NULL, pattern_id TEXT, tags TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(incident_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(incident_id, service, ts);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(incident_id, source, ts);
CREATE TABLE IF NOT EXISTS annotations (
  incident_id TEXT NOT NULL, event_id TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', PRIMARY KEY (incident_id, event_id)
);
"""
SEVERITIES = ["debug", "info", "warning", "error", "critical"]


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def _dt(x: float) -> datetime:
    return datetime.fromtimestamp(x, timezone.utc)


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    # ---------- incidents ----------
    def create_incident(self, title: str, scenario: str | None = None, ground_truth: dict | None = None) -> Incident:
        inc_id = uuid.uuid4().hex[:10]
        now = datetime.now(timezone.utc)
        with self._lock, self.conn() as c:
            c.execute("INSERT INTO incidents (id, title, created_at, scenario, ground_truth) VALUES (?,?,?,?,?)",
                      (inc_id, title, _epoch(now), scenario, json.dumps(ground_truth) if ground_truth else None))
        return Incident(id=inc_id, title=title, created_at=now, scenario=scenario)

    def list_incidents(self) -> list[Incident]:
        rows = self.conn().execute(
            "SELECT i.id, i.title, i.created_at, i.status, i.scenario, "
            "(SELECT COUNT(*) FROM events e WHERE e.incident_id = i.id) FROM incidents i ORDER BY i.created_at DESC").fetchall()
        return [Incident(id=r[0], title=r[1], created_at=_dt(r[2]), status=r[3], scenario=r[4], event_count=r[5]) for r in rows]

    def get_incident(self, inc_id: str) -> Incident | None:
        return next((i for i in self.list_incidents() if i.id == inc_id), None)

    def open_incident(self) -> Incident | None:
        return next((i for i in self.list_incidents() if i.status == "open" and not i.scenario), None)

    def set_status(self, inc_id: str, status: str) -> None:
        with self._lock, self.conn() as c:
            c.execute("UPDATE incidents SET status=? WHERE id=?", (status, inc_id))

    def delete_incident(self, inc_id: str) -> bool:
        with self._lock, self.conn() as c:
            c.execute("DELETE FROM events WHERE incident_id=?", (inc_id,))
            c.execute("DELETE FROM annotations WHERE incident_id=?", (inc_id,))
            return c.execute("DELETE FROM incidents WHERE id=?", (inc_id,)).rowcount > 0

    def ground_truth(self, inc_id: str) -> dict | None:
        r = self.conn().execute("SELECT ground_truth FROM incidents WHERE id=?", (inc_id,)).fetchone()
        return json.loads(r[0]) if r and r[0] else None

    def cache(self, inc_id: str, column: str, value: str | None) -> None:
        assert column in ("analysis", "postmortem")
        with self._lock, self.conn() as c:
            c.execute(f"UPDATE incidents SET {column}=? WHERE id=?", (value, inc_id))

    def cached(self, inc_id: str, column: str) -> str | None:
        assert column in ("analysis", "postmortem")
        r = self.conn().execute(f"SELECT {column} FROM incidents WHERE id=?", (inc_id,)).fetchone()
        return r[0] if r else None

    # ---------- events ----------
    def add_events(self, events: Iterable[Event]) -> int:
        rows = [(e.id, e.incident_id, _epoch(e.ts), e.source, e.service, e.severity, e.message,
                 json.dumps(e.raw, default=str), e.pattern_id, json.dumps(e.tags)) for e in events]
        with self._lock, self.conn() as c:
            c.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            if rows:
                c.execute("UPDATE incidents SET analysis=NULL, postmortem=NULL WHERE id=?", (rows[0][1],))
        return len(rows)

    def set_patterns(self, pairs: list[tuple[str, str]]) -> None:
        with self._lock, self.conn() as c:
            c.executemany("UPDATE events SET pattern_id=? WHERE id=?", [(p, e) for e, p in pairs])

    def query(self, inc_id: str, start: datetime | None = None, end: datetime | None = None,
              source: str | None = None, service: str | None = None, min_severity: str | None = None,
              text: str | None = None, ids: list[str] | None = None, limit: int | None = None,
              offset: int = 0) -> list[Event]:
        sql = "SELECT id, incident_id, ts, source, service, severity, message, raw, pattern_id, tags FROM events WHERE incident_id=?"
        args: list = [inc_id]
        if start:
            sql += " AND ts >= ?"; args.append(_epoch(start))
        if end:
            sql += " AND ts <= ?"; args.append(_epoch(end))
        if source:
            sql += " AND source = ?"; args.append(source)
        if service:
            sql += " AND service = ?"; args.append(service)
        if min_severity:
            allowed = SEVERITIES[SEVERITIES.index(min_severity):]
            sql += f" AND severity IN ({','.join('?' * len(allowed))})"; args += allowed
        if text:
            sql += " AND message LIKE ?"; args.append(f"%{text}%")
        if ids is not None:
            if not ids:
                return []
            sql += f" AND id IN ({','.join('?' * len(ids))})"; args += ids
        sql += " ORDER BY ts, id"
        if limit:
            sql += " LIMIT ? OFFSET ?"; args += [limit, offset]
        return [Event(id=r[0], incident_id=r[1], ts=_dt(r[2]), source=r[3], service=r[4], severity=r[5],
                      message=r[6], raw=json.loads(r[7]), pattern_id=r[8], tags=json.loads(r[9]))
                for r in self.conn().execute(sql, args)]

    def count(self, inc_id: str, source: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM events WHERE incident_id=?", [inc_id]
        if source:
            sql += " AND source=?"; args.append(source)
        return self.conn().execute(sql, args).fetchone()[0]

    def existing_ids(self, ids: list[str]) -> set[str]:
        out: set[str] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            out |= {r[0] for r in self.conn().execute(
                f"SELECT id FROM events WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
        return out

    # ---------- annotations ----------
    def annotate(self, inc_id: str, a: Annotation) -> None:
        with self._lock, self.conn() as c:
            c.execute("INSERT OR REPLACE INTO annotations VALUES (?,?,?,?,?)",
                      (inc_id, a.event_id, int(a.pinned), int(a.hidden), a.note))
            c.execute("UPDATE incidents SET postmortem=NULL WHERE id=?", (inc_id,))

    def annotations(self, inc_id: str) -> dict[str, Annotation]:
        rows = self.conn().execute("SELECT event_id, pinned, hidden, note FROM annotations WHERE incident_id=?", (inc_id,))
        return {r[0]: Annotation(event_id=r[0], pinned=bool(r[1]), hidden=bool(r[2]), note=r[3]) for r in rows}
