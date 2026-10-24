"""Local HTTP server (127.0.0.1 only): OTLP/HTTP JSON receiver, a simple ingest endpoint, the JSON API and the UI.

  POST /v1/logs, /v1/traces   OTLP JSON
  POST /ingest                one record, a list of records, or text lines (one log line each)
  GET  /api/logs?q=&level=&source=&since=&after_id=&limit=
  GET  /api/traces            GET /api/trace/<trace id>      GET /api/stats
  GET  /                      the web page
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import otlp
from .parse import parse_line
from .store import Store

UI = Path(__file__).with_name("ui.html")
MAX_BODY = 16 << 20
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def local_host(header: str | None) -> bool:
    """Only answer requests addressed to this PC by name. A web page that re-points its own domain at 127.0.0.1
    (DNS rebinding) still sends its own domain as Host, so it can't read your logs."""
    host = (header or "").strip().lower()
    if host.startswith("["):
        host = host[:host.find("]") + 1]
    else:
        host = host.split(":", 1)[0]
    return host in LOCAL_HOSTS


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LogHarbor"

        def log_message(self, *args):
            pass

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            if not local_host(self.headers.get("Host")):
                self.send_error(421, "LogHarbor only answers requests for 127.0.0.1 or localhost")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and not local_host(urlsplit(origin).netloc):
                # a web page open in the browser could otherwise post log lines here (plain text needs no permission)
                self.send_error(403, "LogHarbor only takes requests from its own page and from programs")
                return False
            return True

        def _send(self, status: int, payload, content_type: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(400, {"error": "bad Content-Length"})
                return None
            if length > MAX_BODY:
                self._send(413, {"error": "too large"})
                return None
            return self.rfile.read(length)

        def do_POST(self):
            path = urlsplit(self.path).path
            body = self._body()
            if body is None:
                return
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if path in ("/v1/logs", "/v1/traces"):
                if kind != "application/json":
                    self._send(415, {"error": "LogHarbor accepts OTLP as JSON: set OTEL_EXPORTER_OTLP_PROTOCOL=http/json"})
                    return
                try:
                    payload = json.loads(body)
                except ValueError:
                    self._send(400, {"error": "invalid JSON"})
                    return
                if path == "/v1/logs":
                    store.add_logs(otlp.logs(payload))
                else:
                    store.add_spans(otlp.traces(payload))
                self._send(200, {})
                return
            if path == "/ingest":
                source = parse_qs(urlsplit(self.path).query).get("source", ["ingest"])[0]
                text = body.decode("utf-8", errors="replace")
                records = []
                try:
                    data = json.loads(text)
                    items = data if isinstance(data, list) else [data]
                    records = [parse_line(json.dumps(i) if isinstance(i, dict) else str(i), source) for i in items]
                except ValueError:
                    records = [parse_line(line, source) for line in text.splitlines() if line.strip()]
                self._send(200, {"accepted": store.add_logs(records)})
                return
            self._send(404, {"error": "not found"})

        def do_GET(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            if parts.path in ("/", "/index.html"):
                self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            elif parts.path == "/api/logs":
                since = None
                if query.get("since"):
                    since = time.time() - float(query["since"])  # seconds back
                self._send(200, store.search(query.get("q", ""), query.get("level", ""), query.get("source", ""),
                                             since, int(query.get("after_id", 0) or 0), int(query.get("limit", 300))))
            elif parts.path == "/api/traces":
                self._send(200, store.traces())
            elif parts.path.startswith("/api/trace/"):
                self._send(200, store.trace(parts.path.rsplit("/", 1)[1]))
            elif parts.path == "/api/stats":
                self._send(200, store.stats())
            else:
                self._send(404, {"error": "not found"})

    return Handler


def serve(store: Store, port: int = 4318, ui_port: int | None = None) -> ThreadingHTTPServer:
    """Start (in a thread) the receiver on `port`; the UI and API are served on the same port."""
    import threading
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="logharbor-http", daemon=True).start()
    return httpd
