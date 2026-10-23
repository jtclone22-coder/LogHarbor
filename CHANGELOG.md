# Changelog

## 0.1.0 (2026-10-22)

- Follows files, folders and globs live; handles rotation, partial lines and stack traces.
- Parses JSON lines and common text formats; normalises levels and timestamps.
- OTLP/HTTP JSON receiver for logs and traces, a plain /ingest endpoint and a JSON API.
- SQLite storage with full-text search and retention.
- Local web page (light/dark): search, level and time filters, live tail, trace list and waterfall. Command line: serve, search, stats, prune. Demo mode.
- Answers only requests addressed to 127.0.0.1 or localhost, so a web page can't read your logs through DNS rebinding.
- Run with no command (for example double-clicked), it starts the server and opens the page.
