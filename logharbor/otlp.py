"""OpenTelemetry OTLP/HTTP JSON (logs and traces) -> records and spans.

SDKs send protobuf by default; set OTEL_EXPORTER_OTLP_PROTOCOL=http/json to send JSON to LogHarbor.
"""
from __future__ import annotations

from .parse import normalize_level


def _value(any_value: dict):
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in any_value:
            value = any_value[key]
            return int(value) if key == "intValue" else value
    if "arrayValue" in any_value:
        return [_value(v) for v in any_value["arrayValue"].get("values", [])]
    if "kvlistValue" in any_value:
        return _attributes(any_value["kvlistValue"].get("values", []))
    return None


def _attributes(items) -> dict:
    return {item.get("key", ""): _value(item.get("value") or {}) for item in items or []}


def _nanos(value) -> float:
    return int(value or 0) / 1e9


def logs(payload: dict) -> list[dict]:
    records = []
    for resource_logs in payload.get("resourceLogs", []):
        resource = _attributes((resource_logs.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name", "otel"))
        for scope_logs in resource_logs.get("scopeLogs", []):
            for record in scope_logs.get("logRecords", []):
                body = _value(record.get("body") or {})
                attrs = _attributes(record.get("attributes"))
                level = record.get("severityText") or record.get("severityNumber") or "INFO"
                stamp = _nanos(record.get("timeUnixNano")) or _nanos(record.get("observedTimeUnixNano"))
                records.append({"ts": stamp, "level": normalize_level(level), "message": str(body if body is not None else ""),
                                "source": service, "attrs": attrs, "trace_id": record.get("traceId", ""),
                                "span_id": record.get("spanId", "")})
    return records


STATUS = {0: "UNSET", 1: "OK", 2: "ERROR", "STATUS_CODE_ERROR": "ERROR", "STATUS_CODE_OK": "OK"}


def traces(payload: dict) -> list[dict]:
    spans = []
    for resource_spans in payload.get("resourceSpans", []):
        resource = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name", "otel"))
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                code = (span.get("status") or {}).get("code", 0)
                spans.append({"trace_id": span.get("traceId", ""), "span_id": span.get("spanId", ""),
                              "parent_id": span.get("parentSpanId", ""), "name": span.get("name", ""),
                              "service": service, "start": _nanos(span.get("startTimeUnixNano")),
                              "end": _nanos(span.get("endTimeUnixNano")), "status": STATUS.get(code, str(code)),
                              "attrs": _attributes(span.get("attributes"))})
    return spans
