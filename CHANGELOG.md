# Changelog

## 0.1.1 (2026-10-24)

Fixes from a code review.

- Security: a log line could put a script into the viewer page through an unknown log level, and any web page open in the browser could post log lines. Levels are now letters and digits only and escaped, and requests from other sites are refused.
- Following files no longer stops for good when a log file is locked by its writer or removed for a moment.
- A negative Content-Length is refused instead of holding the connection.

## 0.1.0 (2026-10-22)

- Follows files, folders and globs live; handles rotation, partial lines and stack traces.
- Parses JSON lines and common text formats; normalises levels and timestamps.
- OTLP/HTTP JSON receiver for logs and traces, a plain /ingest endpoint and a JSON API.
- SQLite storage with full-text search and retention.
- Local web page (light/dark): search, level and time filters, live tail, trace list and waterfall. Command line: serve, search, stats, prune. Demo mode.
- Answers only requests addressed to 127.0.0.1 or localhost, so a web page can't read your logs through DNS rebinding.
- Run with no command (for example double-clicked), it starts the server and opens the page.
