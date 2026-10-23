# LogHarbor

**All your app logs and traces in one searchable place, on your own PC.** There is no cloud account, no Docker and nothing to install but Python. LogHarbor:
- **follows your log files live**, and understands JSON lines plus the usual text formats (Python, Go, Java, Node), keeping stack traces with their error;
- **receives OpenTelemetry** logs and traces;
- **shows them in a local web page** with full-text search, a level filter, a time range, a live tail and a trace waterfall.

## Download

Get **LogHarbor-0.1.0-windows.zip** from [Releases](../../releases/latest), unzip it anywhere and double-click **LogHarbor.exe** in the LogHarbor folder. It starts LogHarbor and opens the page in your browser; keep its window open while you use it (close it to stop). No Python needed.

To follow log files, start it from a terminal in that folder instead: `LogHarbor.exe serve --follow D:\code\my-api\logs --open`.

Windows may say "Windows protected your PC" because the app is new: click **More info**, then **Run anyway**.

Prefer Python? With Python 3.9 or newer, download this repository and run `py -m logharbor` in its folder.

## Start

```
py -m logharbor serve --follow D:\code\my-api\logs C:\apps\worker\*.log --open
```

Then open http://127.0.0.1:4318. It listens on 127.0.0.1 only, so nothing is exposed to your network. You can send logs to it in three ways:

| From | How |
|---|---|
| Log files | `--follow` files, folders (`*.log`, `*.jsonl`, …) or globs. It survives rotation and truncation. |
| OpenTelemetry SDKs | `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` and `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` |
| Anything else | `POST http://127.0.0.1:4318/ingest?source=myapp` with a JSON object, a JSON list, or plain text lines |

Want to see it first? `py run_demo.py` starts it with sample logs and traces.

## Search from a terminal

```
py -m logharbor search payment failed --level error --since 2h
py -m logharbor stats
py -m logharbor prune --keep-days 7
```

## Details

- **Storage:** SQLite with full-text search in `%LOCALAPPDATA%\LogHarbor`, kept for 14 days by default (`--keep-days`).
- **Levels:** TRACE, DEBUG, INFO, WARN, ERROR and FATAL, normalised from common spellings and OpenTelemetry severity numbers.
- **Protobuf:** OTLP protobuf isn't supported in this version. LogHarbor answers 415 with the setting to switch to JSON.

## Tests
```
py -m unittest discover -s tests
```

MIT License.
