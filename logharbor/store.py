"""SQLite store for logs and spans with full-text search and size/age retention."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, ts REAL, level TEXT, source TEXT, message TEXT,
                                attrs TEXT, trace_id TEXT, span_id TEXT);
CREATE INDEX IF NOT EXISTS logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS logs_trace ON logs(trace_id);
CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts USING fts5(message, attrs, source, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS spans(trace_id TEXT, span_id TEXT, parent_id TEXT, name TEXT, service TEXT,
                                 start REAL, end REAL, status TEXT, attrs TEXT, PRIMARY KEY(trace_id, span_id));
CREATE INDEX IF NOT EXISTS spans_start ON spans(start);
"""
LEVELS = ["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]


def home() -> Path:
    base = os.environ.get("LOGHARBOR_HOME") or (
        os.path.join(os.environ["LOCALAPPDATA"], "LogHarbor") if os.environ.get("LOCALAPPDATA")
        else os.path.join(os.path.expanduser("~"), ".logharbor"))
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base)


def fts_query(text: str) -> str | None:
    words = re.findall(r"[\w.@/-]+", text or "")
    return " AND ".join('"' + w.replace('"', "") + '"' for w in words) if words else None


class Store:
    def __init__(self, path: Path | None = None, keep_days: float = 14.0, max_rows: int = 2_000_000):
        self.path = path or home() / "logs.sqlite"
        self.keep_days, self.max_rows = keep_days, max_rows
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add_logs(self, records: list[dict]) -> int:
        with self.lock:
            for r in records:
                attrs = json.dumps(r.get("attrs") or {}, default=str)
                row_id = self.db.execute(
                    "INSERT INTO logs(ts, level, source, message, attrs, trace_id, span_id) VALUES(?,?,?,?,?,?,?)",
                    (r["ts"], r["level"], r["source"], r["message"], attrs, r.get("trace_id", ""),
                     r.get("span_id", ""))).lastrowid
                self.db.execute("INSERT INTO logs_fts(rowid, message, attrs, source) VALUES(?,?,?,?)",
                                (row_id, r["message"], attrs, r["source"]))
            self.db.commit()
        return len(records)

    def append_to_last(self, source: str, extra: str) -> None:
        """Attach a stack-trace line to the newest record from the same source."""
        with self.lock:
            row = self.db.execute("SELECT id, message FROM logs WHERE source=? ORDER BY id DESC LIMIT 1",
                                  (source,)).fetchone()
            if row is None:
                return
            message = row[1] + "\n" + extra
            self.db.execute("UPDATE logs SET message=? WHERE id=?", (message, row[0]))
            self.db.execute("UPDATE logs_fts SET message=? WHERE rowid=?", (message, row[0]))
            self.db.commit()

    def add_spans(self, spans: list[dict]) -> int:
        with self.lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO spans VALUES(?,?,?,?,?,?,?,?,?)",
                [(s["trace_id"], s["span_id"], s.get("parent_id", ""), s["name"], s.get("service", ""), s["start"],
                  s["end"], s.get("status", ""), json.dumps(s.get("attrs") or {}, default=str)) for s in spans])
            self.db.commit()
        return len(spans)

    def search(self, text: str = "", level: str = "", source: str = "", since: float | None = None,
               after_id: int = 0, limit: int = 200) -> list[dict]:
        where, args = ["l.id > ?"], [after_id]
        if level and level.upper() in LEVELS:
            where.append("l.level IN (%s)" % ",".join("?" * len(LEVELS[LEVELS.index(level.upper()):])))
            args += LEVELS[LEVELS.index(level.upper()):]
        if source:
            where.append("l.source LIKE ?")
            args.append(f"%{source}%")
        if since is not None:
            where.append("l.ts >= ?")
            args.append(since)
        join = ""
        expression = fts_query(text)
        if expression:
            join = "JOIN logs_fts f ON f.rowid = l.id"
            where.append("logs_fts MATCH ?")
            args.append(expression)
        rows = self.db.execute(
            f"SELECT l.id, l.ts, l.level, l.source, l.message, l.attrs, l.trace_id, l.span_id FROM logs l {join} "
            f"WHERE {' AND '.join(where)} ORDER BY l.id DESC LIMIT ?", (*args, max(1, min(limit, 2000)))).fetchall()
        return [{"id": r[0], "ts": r[1], "level": r[2], "source": r[3], "message": r[4], "attrs": json.loads(r[5]),
                 "trace_id": r[6], "span_id": r[7]} for r in rows]

    def traces(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute(
            "SELECT trace_id, MIN(start), MAX(end), COUNT(*), "
            "SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END), "
            "(SELECT name FROM spans s2 WHERE s2.trace_id = s.trace_id AND (s2.parent_id = '' OR s2.parent_id IS NULL) LIMIT 1), "
            "(SELECT service FROM spans s3 WHERE s3.trace_id = s.trace_id LIMIT 1) "
            "FROM spans s GROUP BY trace_id ORDER BY MIN(start) DESC LIMIT ?", (limit,)).fetchall()
        return [{"trace_id": r[0], "start": r[1], "duration_ms": round((r[2] - r[1]) * 1000, 2), "spans": r[3],
                 "errors": r[4], "root": r[5] or "", "service": r[6] or ""} for r in rows]

    def trace(self, trace_id: str) -> list[dict]:
        rows = self.db.execute("SELECT span_id, parent_id, name, service, start, end, status, attrs FROM spans "
                               "WHERE trace_id=? ORDER BY start", (trace_id,)).fetchall()
        return [{"span_id": r[0], "parent_id": r[1], "name": r[2], "service": r[3], "start": r[4], "end": r[5],
                 "status": r[6], "attrs": json.loads(r[7])} for r in rows]

    def stats(self) -> dict:
        count, first, last = self.db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM logs").fetchone()
        by_level = dict(self.db.execute("SELECT level, COUNT(*) FROM logs GROUP BY level").fetchall())
        sources = [r[0] for r in self.db.execute("SELECT DISTINCT source FROM logs ORDER BY source LIMIT 200")]
        spans = self.db.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        return {"logs": count, "first": first, "last": last, "levels": by_level, "sources": sources, "spans": spans}

    def prune(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.keep_days * 86400
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM logs WHERE ts < ?", (cutoff,))]
            extra = self.db.execute("SELECT COUNT(*) FROM logs").fetchone()[0] - len(ids) - self.max_rows
            if extra > 0:
                ids += [r[0] for r in self.db.execute(
                    "SELECT id FROM logs WHERE ts >= ? ORDER BY id LIMIT ?", (cutoff, extra))]
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                self.db.execute(f"DELETE FROM logs WHERE id IN ({marks})", chunk)
                self.db.execute(f"DELETE FROM logs_fts WHERE rowid IN ({marks})", chunk)
            self.db.execute("DELETE FROM spans WHERE start < ?", (cutoff,))
            self.db.commit()
        return len(ids)
