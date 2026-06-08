from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from compact_sidecar.runtime.operation_log import append_operation
from compact_sidecar.config import CONFIG_PATH_ENV, SidecarConfigError, load_config_for_import, load_config_safe
from compact_sidecar.paths import runtime_path, write_error

_CONFIG = load_config_for_import()
MAX_HISTORY_BYTES = int(_CONFIG["history_candidates"]["history_max_bytes"])
MAX_PAYLOAD_CHARS = int(_CONFIG["hooks"]["postcompact_payload_max_chars"])
HISTORY_NAME = str(_CONFIG["paths"]["runtime_files"]["compact_history"])
ROTATED_HISTORY_NAME = str(_CONFIG["paths"]["runtime_files"]["compact_history_rotated"])
OPERATION_LOG_DEFAULT = bool(_CONFIG["operation_log"].get("enabled_by_default"))
RAW_SUMMARY_DEFAULT = bool(_CONFIG["operation_log"].get("raw_summary_logged_by_default"))


def refresh_config() -> None:
    global _CONFIG, MAX_HISTORY_BYTES, MAX_PAYLOAD_CHARS, HISTORY_NAME, ROTATED_HISTORY_NAME, OPERATION_LOG_DEFAULT, RAW_SUMMARY_DEFAULT
    _CONFIG = load_config_safe()
    MAX_HISTORY_BYTES = int(_CONFIG["history_candidates"]["history_max_bytes"])
    MAX_PAYLOAD_CHARS = int(_CONFIG["hooks"]["postcompact_payload_max_chars"])
    HISTORY_NAME = str(_CONFIG["paths"]["runtime_files"]["compact_history"])
    ROTATED_HISTORY_NAME = str(_CONFIG["paths"]["runtime_files"]["compact_history_rotated"])
    OPERATION_LOG_DEFAULT = bool(_CONFIG["operation_log"].get("enabled_by_default"))
    RAW_SUMMARY_DEFAULT = bool(_CONFIG["operation_log"].get("raw_summary_logged_by_default"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record Claude Code PostCompact hook payload.")
    parser.add_argument("--config", help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.")
    return parser.parse_args(argv)


def operation_log_enabled() -> bool:
    return OPERATION_LOG_DEFAULT or os.environ.get("SIDECAR_OPERATION_LOG") == "1" or os.environ.get("SIDECAR_LOG_RAW_SUMMARY") == "1"


def raw_summary_enabled() -> bool:
    return RAW_SUMMARY_DEFAULT or os.environ.get("SIDECAR_LOG_RAW_SUMMARY") == "1"


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

    rotated_path = history_path.with_name(ROTATED_HISTORY_NAME)
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
    history_path = runtime_path(HISTORY_NAME)
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.config:
        os.environ[CONFIG_PATH_ENV] = args.config
    try:
        refresh_config()
    except SidecarConfigError:
        return 0
    payload = read_payload()
    if payload is None:
        return 0

    try:
        metadata = append_history(payload)
    except Exception as exc:
        write_error(f"failed to append {HISTORY_NAME}", exc=exc, service="postcompact")
        log_postcompact_operation("append-history", "error", {"error": "append_failed", "error_kind": type(exc).__name__}, payload)
        return 0

    log_postcompact_operation("append-history", "ok", metadata, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
