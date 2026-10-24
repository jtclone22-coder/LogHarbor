"""Turn log lines into records: JSON lines, Python/Java/Go-style text logs, or plain text.

A record is a dict: ts (epoch seconds), level, message, source, attrs (dict), trace_id, span_id.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

LEVELS = {
    "trace": "TRACE", "verbose": "TRACE", "debug": "DEBUG", "dbug": "DEBUG", "info": "INFO", "information": "INFO",
    "notice": "INFO", "warn": "WARN", "warning": "WARN", "error": "ERROR", "err": "ERROR", "fatal": "FATAL",
    "critical": "FATAL", "crit": "FATAL", "panic": "FATAL", "severe": "ERROR",
}
LEVEL_ORDER = ["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
MESSAGE_KEYS = ("message", "msg", "event", "text", "log")
LEVEL_KEYS = ("level", "levelname", "severity", "lvl", "log.level", "loglevel")
TIME_KEYS = ("timestamp", "@timestamp", "time", "ts", "datetime", "date", "asctime")

TIMESTAMP = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)")
LEVEL_WORD = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL|PANIC|SEVERE)\b", re.I)
CONTINUATION = re.compile(r"^(\s+\S|Traceback \(most recent call last\)|Caused by:|\S+(Error|Exception)(:|$))")


def normalize_level(value) -> str:
    if isinstance(value, (int, float)):  # syslog-ish or OTel severity numbers
        number = int(value)
        if number >= 21: return "FATAL"
        if number >= 17: return "ERROR"
        if number >= 13: return "WARN"
        if number >= 9: return "INFO"
        if number >= 5: return "DEBUG"
        return "TRACE"
    if not value:
        return "INFO"
    known = LEVELS.get(str(value).strip().lower())
    # an unknown level is kept, but only as letters and digits: it goes into the page, and a log line is outside text
    return known or re.sub(r"[^A-Z0-9_]", "", str(value).upper())[:8] or "INFO"


def parse_time(value) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e17: return number / 1e9   # nanoseconds
        if number > 1e14: return number / 1e6   # microseconds
        if number > 1e11: return number / 1e3   # milliseconds
        return number
    if not isinstance(value, str):
        return None
    match = TIMESTAMP.search(value)
    if not match:
        return None
    text = match.group("ts").replace(",", ".").replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    head, dot, fraction = text.partition(".")
    if dot:
        digits = re.match(r"\d+", fraction).group(0)
        text = f"{head}.{digits[:6].ljust(6, '0')}{fraction[len(digits):]}"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # naive timestamps are local time
    return moment.timestamp()


def parse_line(line: str, source: str, now: float | None = None) -> dict:
    line = line.rstrip("\r\n")
    now = time.time() if now is None else now
    stripped = line.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            message = next((str(data.pop(k)) for k in MESSAGE_KEYS if k in data), "")
            level = next((data.pop(k) for k in LEVEL_KEYS if k in data), None)
            stamp = next((parse_time(data.pop(k)) for k in TIME_KEYS if k in data), None)
            trace_id = str(data.pop("trace_id", data.pop("traceId", "")) or "")
            span_id = str(data.pop("span_id", data.pop("spanId", "")) or "")
            return {"ts": stamp or now, "level": normalize_level(level), "message": message or stripped,
                    "source": source, "attrs": data, "trace_id": trace_id, "span_id": span_id}
    stamp = parse_time(line[:40])
    level_match = LEVEL_WORD.search(line[:120])
    level = normalize_level(level_match.group(1)) if level_match else "INFO"
    message = line
    if level_match:  # drop the prefix up to and including the level word and common separators
        message = line[level_match.end():].lstrip(" :|-]")
    elif stamp is not None:
        message = TIMESTAMP.sub("", line, count=1).lstrip(" :|-]")
    return {"ts": stamp or now, "level": level, "message": message or line, "source": source, "attrs": {},
            "trace_id": "", "span_id": ""}


def is_continuation(line: str) -> bool:
    """Stack-trace lines and indented follow-ups belong to the record before them."""
    return bool(line.strip()) and bool(CONTINUATION.match(line)) and not TIMESTAMP.match(line.strip()[:30])
