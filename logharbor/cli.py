"""Command line: serve (receiver + web page + file following), search, stats, prune."""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser

from . import __version__
from .parse import LEVEL_ORDER
from .server import serve
from .store import Store
from .tail import Follower


def parse_since(text: str | None) -> float | None:
    if not text:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    value, unit = text[:-1], text[-1].lower()
    if unit not in units:
        value, unit = text, "s"
    return time.time() - float(value) * units[unit]


def seed_demo(store: Store) -> None:
    """Realistic sample data for trying the page out (and for screenshots)."""
    import random
    from .parse import parse_line
    rng = random.Random(7)
    now = time.time()
    lines = []
    for i in range(60):
        t = now - (60 - i) * 37
        source, text = rng.choice([
            ("api", "INFO GET /orders/{n} 200 in {ms} ms"), ("api", "INFO POST /checkout 201 in {ms} ms"),
            ("worker", "DEBUG picked job {n} from queue"), ("worker", "INFO email sent to customer {n}"),
            ("api", "WARN slow query on orders ({ms} ms)"), ("scheduler", "INFO nightly backup step {n} done")])
        lines.append(dict(parse_line(text.format(n=rng.randint(100, 999), ms=rng.randint(4, 900)), source, t), ts=t))
    failure = dict(parse_line("ERROR payment failed for order 4471", "api", now - 90), ts=now - 90)
    failure["message"] += ("\nTraceback (most recent call last):\n  File \"billing.py\", line 88, in charge\n"
                           "    gateway.capture(order)\nTimeoutError: card gateway did not answer in 10 s")
    failure["attrs"] = {"order": 4471, "customer": "c-1288"}
    store.add_logs(sorted(lines + [failure], key=lambda r: r["ts"]))  # arrive in time order, like a real app
    spans = []
    for k, (name, total, error) in enumerate((("POST /checkout", 0.42, False), ("POST /pay", 10.3, True),
                                              ("GET /orders", 0.08, False))):
        start = now - 300 + k * 60
        trace = f"demo{k}"
        spans.append({"trace_id": trace, "span_id": "a", "parent_id": "", "name": name, "service": "api",
                      "start": start, "end": start + total, "status": "ERROR" if error else "OK"})
        spans.append({"trace_id": trace, "span_id": "b", "parent_id": "a", "name": "db query", "service": "api",
                      "start": start + 0.01, "end": start + 0.05, "status": "OK"})
        spans.append({"trace_id": trace, "span_id": "c", "parent_id": "a", "name": "card gateway" if k == 1 else "render",
                      "service": "billing" if k == 1 else "api", "start": start + 0.06, "end": start + total - 0.01,
                      "status": "ERROR" if error else "OK"})
    store.add_spans(spans)


def cmd_serve(args) -> int:
    from pathlib import Path
    store = Store(Path(args.db) if args.db else None, keep_days=args.keep_days)
    if args.demo:
        seed_demo(store)
    httpd = serve(store, args.port)
    follower = Follower(store, args.follow, from_start=args.from_start) if args.follow else None
    if follower:
        follower.start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"LogHarbor is running: open {url}")
    print(f"  OpenTelemetry: OTEL_EXPORTER_OTLP_ENDPOINT={url.rstrip('/')}  OTEL_EXPORTER_OTLP_PROTOCOL=http/json")
    print(f"  Plain HTTP:    POST {url}ingest?source=myapp  (JSON or text lines)")
    if args.follow:
        print(f"  Following:     {', '.join(args.follow)}")
    if args.open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
            store.prune()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("Stopped.")
    return 0


def cmd_search(args) -> int:
    store = Store()
    for row in reversed(store.search(" ".join(args.words), args.level or "", args.source or "", parse_since(args.since),
                                     limit=args.limit)):
        stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(row["ts"]))
        print(f"{stamp} {row['level']:<5} {row['source']:<16} {row['message']}")
    return 0


def cmd_stats(args) -> int:
    stats = Store().stats()
    print(f"{stats['logs']:,} log lines, {stats['spans']:,} spans; sources: {', '.join(stats['sources']) or 'none'}")
    for level in LEVEL_ORDER:
        if stats["levels"].get(level):
            print(f"  {level:<5} {stats['levels'][level]:,}")
    return 0


def cmd_prune(args) -> int:
    print(f"Removed {Store(keep_days=args.keep_days).prune():,} old log lines.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="logharbor", description="Your logs and traces, searchable, on your own PC.")
    parser.add_argument("--version", action="version", version=f"LogHarbor {__version__}")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("serve", help="start the receiver and web page; optionally follow log files")
    p.add_argument("--port", type=int, default=4318)
    p.add_argument("--follow", nargs="*", default=[], help="files, folders or globs to follow")
    p.add_argument("--from-start", action="store_true", help="also read what's already in followed files")
    p.add_argument("--keep-days", type=float, default=14.0)
    p.add_argument("--open", action="store_true", help="open the page in the browser")
    p.add_argument("--db", help="database file (default: %%LOCALAPPDATA%%/LogHarbor/logs.sqlite)")
    p.add_argument("--demo", action="store_true", help="add sample logs and traces to try it out")
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("search", help="search stored logs")
    p.add_argument("words", nargs="*")
    p.add_argument("--level")
    p.add_argument("--source")
    p.add_argument("--since", help="e.g. 15m, 2h, 1d")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_search)
    sub.add_parser("stats", help="what's stored").set_defaults(func=cmd_stats)
    p = sub.add_parser("prune", help="delete logs older than --keep-days")
    p.add_argument("--keep-days", type=float, default=14.0)
    p.set_defaults(func=cmd_prune)
    args = parser.parse_args(argv)
    if not args.command:  # double-clicked (or no command): start the server and open the page
        args = parser.parse_args(["serve", "--open"])
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
