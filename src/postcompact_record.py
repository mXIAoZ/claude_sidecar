from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from operation_log import append_operation
from sidecar_paths import runtime_path, write_error

MAX_HISTORY_BYTES = 5_000_000
MAX_PAYLOAD_CHARS = 200_000


def operation_log_enabled() -> bool:
    return os.environ.get("SIDECAR_OPERATION_LOG") == "1" or os.environ.get("SIDECAR_LOG_RAW_SUMMARY") == "1"


def raw_summary_enabled() -> bool:
    return os.environ.get("SIDECAR_LOG_RAW_SUMMARY") == "1"


def extract_summary(payload: dict) -> str:
    summary = payload.get("summary")
    if isinstance(summary, str):
        return summary
    return ""


def log_postcompact_operation(operation: str, status: str, metadata: dict, payload: dict | None = None) -> None:
    if not operation_log_enabled():
        return
    summary = extract_summary(payload or {})
    raw = {"summary": summary} if raw_summary_enabled() and summary else None
    append_operation(
        "postcompact",
        operation,
        status,
        metadata=metadata,
        raw=raw,
        content_policy={"raw_prompt_logged": False, "raw_summary_logged": bool(raw)},
    )


def read_payload() -> dict | None:
    raw = sys.stdin.read(MAX_PAYLOAD_CHARS + 1)
    if len(raw) > MAX_PAYLOAD_CHARS:
        write_error("PostCompact hook payload exceeded size limit", service="postcompact")
        log_postcompact_operation("read-payload", "error", {"error": "payload_exceeded_size_limit", "input_chars": len(raw)})
        return None

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        write_error("failed to parse PostCompact hook payload", exc=exc, service="postcompact")
        log_postcompact_operation("read-payload", "error", {"error": "json_decode", "error_kind": type(exc).__name__})
        return None

    if isinstance(payload, dict):
        return payload

    write_error("PostCompact hook payload was not a JSON object", service="postcompact")
    log_postcompact_operation("read-payload", "error", {"error": "non_object_payload", "payload_type": type(payload).__name__})
    return None


def rotate_history_if_needed(history_path, record_line: str) -> bool:
    record_bytes = len(record_line.encode("utf-8"))
    if not history_path.exists() or history_path.stat().st_size + record_bytes <= MAX_HISTORY_BYTES:
        return False

    rotated_path = history_path.with_name(history_path.name + ".1")
    rotated_path.unlink(missing_ok=True)
    history_path.replace(rotated_path)
    return True


def append_history(payload: dict) -> dict:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload_bytes": len(payload_json.encode("utf-8")),
        "payload": payload,
    }
    record_line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    history_path = runtime_path("compact-history.jsonl")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rotated = rotate_history_if_needed(history_path, record_line)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(record_line)
    return {
        "history_file": history_path.name,
        "payload_bytes": record["payload_bytes"],
        "summary_chars": len(extract_summary(payload)),
        "rotated": rotated,
    }


def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    try:
        metadata = append_history(payload)
    except Exception as exc:
        write_error("failed to append compact-history.jsonl", exc=exc, service="postcompact")
        log_postcompact_operation("append-history", "error", {"error": "append_failed", "error_kind": type(exc).__name__}, payload)
        return 0

    log_postcompact_operation("append-history", "ok", metadata, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
