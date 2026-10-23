"""LogHarbor tests: parsing, following files, OTLP JSON, store/search/retention, and the HTTP server on loopback."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logharbor import otlp  # noqa: E402
from logharbor.parse import is_continuation, normalize_level, parse_line, parse_time  # noqa: E402
from logharbor.server import serve  # noqa: E402
from logharbor.store import Store  # noqa: E402
from logharbor.tail import Follower  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_json_lines(self) -> None:
        r = parse_line('{"time":"2026-10-22T13:00:00.5Z","level":"warning","msg":"disk 91% full","disk":"C:"}', "app")
        self.assertEqual((r["level"], r["message"], r["attrs"]), ("WARN", "disk 91% full", {"disk": "C:"}))
        self.assertEqual(r["ts"], datetime(2026, 10, 22, 13, 0, 0, 500000, tzinfo=timezone.utc).timestamp())
        r = parse_line('{"ts": 1761138000123, "severity": 17, "message": "boom", "trace_id": "abc"}', "svc")
        self.assertEqual((r["level"], r["ts"], r["trace_id"]), ("ERROR", 1761138000.123, "abc"))

    def test_text_formats(self) -> None:
        r = parse_line("2026-10-22 13:05:01,250 - billing - ERROR - card declined", "py")
        self.assertEqual((r["level"], r["message"]), ("ERROR", "card declined"))
        self.assertAlmostEqual(r["ts"] % 60, 1.25, places=3)
        r = parse_line("[2026-10-22T13:05:01Z] INFO server listening on :8080", "go")
        self.assertEqual((r["level"], r["message"]), ("INFO", "server listening on :8080"))
        r = parse_line("just some text", "x", now=123.0)
        self.assertEqual((r["level"], r["ts"], r["message"]), ("INFO", 123.0, "just some text"))
        self.assertEqual(parse_time("2026-10-22T13:00:00+0200"), datetime(2026, 10, 22, 11, tzinfo=timezone.utc).timestamp())

    def test_levels_and_continuations(self) -> None:
        self.assertEqual([normalize_level(x) for x in ("err", "Critical", 9, 22, "")], ["ERROR", "FATAL", "INFO", "FATAL", "INFO"])
        self.assertTrue(is_continuation('  File "app.py", line 3, in <module>'))
        self.assertTrue(is_continuation("Traceback (most recent call last):"))
        self.assertTrue(is_continuation("ValueError: bad input"))
        self.assertFalse(is_continuation("2026-10-22 13:05:01 INFO next record"))


class StoreAndTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "logs.sqlite")
        self.addCleanup(self.store.close)
        self.log = Path(self.temp.name) / "app.log"

    def test_follow_rotation_partial_lines_and_stack_traces(self) -> None:
        self.log.write_text("2026-10-22 13:00:00 INFO before start\n", encoding="utf-8")
        follower = Follower(self.store, [self.temp.name])  # a folder: *.log inside
        self.assertEqual(follower.step(), 0)  # existing content is skipped by default
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write("2026-10-22 13:00:01 ERROR payment failed\nTraceback (most recent call last):\n"
                         '  File "pay.py", line 9, in charge\nValueError: card declined\n2026-10-22 13:00:02 INFO half')
        self.assertEqual(follower.step(), 1)
        rows = self.store.search()
        self.assertEqual(rows[0]["level"], "ERROR")
        self.assertIn("ValueError: card declined", rows[0]["message"])
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(" a line\n  continued later\n")
        follower.step()
        self.assertEqual(self.store.search()[0]["message"], "half a line\n  continued later")
        self.log.write_text("2026-10-22 13:10:00 WARN after rotation\n", encoding="utf-8")  # truncated + rewritten
        follower.step()
        self.assertEqual(self.store.search()[0]["message"], "after rotation")

    def test_search_filters_and_retention(self) -> None:
        now = time.time()
        self.store.add_logs([parse_line(l, s, now) for s, l in (
            ("api", "INFO user 42 logged in"), ("api", "ERROR user 42 payment failed"),
            ("worker", "WARN queue backlog 900"), ("worker", "DEBUG tick"))])
        self.store.add_logs([dict(parse_line("INFO ancient", "api", now - 30 * 86400), ts=now - 30 * 86400)])
        self.assertEqual([r["message"] for r in self.store.search("user 42")],
                         ["user 42 payment failed", "user 42 logged in"])
        self.assertEqual([r["level"] for r in self.store.search(level="WARN")], ["WARN", "ERROR"])
        self.assertEqual(len(self.store.search(source="work")), 2)
        self.assertEqual(len(self.store.search(since=now - 60)), 4)
        self.store.search('"quotes" AND OR NEAR(')  # free text never breaks the query
        self.assertEqual(self.store.prune(), 1)
        self.assertEqual(self.store.stats()["logs"], 4)


OTLP_LOGS = {"resourceLogs": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]},
                               "scopeLogs": [{"logRecords": [{"timeUnixNano": "1761138000000000000", "severityText": "ERROR",
                                                              "body": {"stringValue": "charge failed"},
                                                              "attributes": [{"key": "order", "value": {"intValue": "77"}}],
                                                              "traceId": "t1", "spanId": "s2"}]}]}]}
OTLP_TRACES = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]},
                                  "scopeSpans": [{"spans": [
                                      {"traceId": "t1", "spanId": "s1", "name": "POST /pay",
                                       "startTimeUnixNano": "1761138000000000000", "endTimeUnixNano": "1761138000250000000"},
                                      {"traceId": "t1", "spanId": "s2", "parentSpanId": "s1", "name": "charge card",
                                       "startTimeUnixNano": "1761138000050000000", "endTimeUnixNano": "1761138000200000000",
                                       "status": {"code": 2}}]}]}]}


class OtlpAndServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "logs.sqlite")
        self.addCleanup(self.store.close)

    def test_otlp_json(self) -> None:
        record = otlp.logs(OTLP_LOGS)[0]
        self.assertEqual((record["source"], record["level"], record["message"], record["attrs"]),
                         ("checkout", "ERROR", "charge failed", {"order": 77}))
        spans = otlp.traces(OTLP_TRACES)
        self.assertEqual([(s["name"], s["status"]) for s in spans], [("POST /pay", "UNSET"), ("charge card", "ERROR")])

    def test_http_endpoints(self) -> None:
        httpd = serve(self.store, 0)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post(path, body, kind="application/json"):
            request = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": kind})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        self.assertEqual(post("/v1/logs", json.dumps(OTLP_LOGS).encode())[0], 200)
        self.assertEqual(post("/v1/traces", json.dumps(OTLP_TRACES).encode())[0], 200)
        status, body = post("/v1/logs", b"\x0a\x00", "application/x-protobuf")
        self.assertEqual(status, 415)
        self.assertIn("http/json", body["error"])
        self.assertEqual(post("/ingest?source=cron", b"2026-10-22 13:00:00 WARN backup slow\nplain line\n",
                              "text/plain")[1], {"accepted": 2})
        get = lambda path: json.loads(urllib.request.urlopen(base + path, timeout=5).read())  # noqa: E731
        self.assertEqual([r["message"] for r in get("/api/logs?level=WARN")], ["backup slow", "charge failed"])
        traces = get("/api/traces")
        self.assertEqual((traces[0]["root"], traces[0]["duration_ms"], traces[0]["errors"]), ("POST /pay", 250.0, 1))
        self.assertEqual(len(get("/api/trace/t1")), 2)
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        self.assertIn("<title>LogHarbor</title>", page)
        rebound = urllib.request.Request(base + "/api/logs", headers={"Host": "evil.example:4318"})
        with self.assertRaises(urllib.error.HTTPError) as caught:  # DNS rebinding: a foreign Host is refused
            urllib.request.urlopen(rebound, timeout=5)
        self.assertEqual(caught.exception.code, 421)
        localhost = urllib.request.Request(base + "/api/stats", headers={"Host": f"localhost:{httpd.server_address[1]}"})
        self.assertEqual(urllib.request.urlopen(localhost, timeout=5).status, 200)


if __name__ == "__main__":
    unittest.main()
