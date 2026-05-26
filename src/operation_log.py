from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sidecar_paths import runtime_path

OPERATION_LOG = "operation-log.jsonl"
ROTATED_OPERATION_LOG = "operation-log.jsonl.1"
SCHEMA_VERSION = 1
MAX_OPERATION_LOG_BYTES = 5_000_000
MAX_RAW_CONTENT_CHARS = 200_000


def bounded_raw_text(text: str) -> str:
    return text[:MAX_RAW_CONTENT_CHARS]


def rotate_if_needed(path: Path, *, incoming_bytes: int = 0, max_bytes: int | None = None) -> bool:
    limit = MAX_OPERATION_LOG_BYTES if max_bytes is None else max_bytes
    try:
        if path.stat().st_size + incoming_bytes <= limit:
            return False
    except OSError:
        return False
    rotated = path.with_name(ROTATED_OPERATION_LOG)
    try:
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    except OSError:
        return False
    return True


def append_operation(
    service: str,
    operation: str,
    status: str,
    *,
    metadata: dict[str, Any] | None = None,
    raw: dict[str, str] | None = None,
    content_policy: dict[str, bool] | None = None,
) -> None:
    try:
        path = runtime_path(OPERATION_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "operation": operation,
            "status": status,
            "metadata": metadata or {},
            "content_policy": content_policy
            or {
                "raw_prompt_logged": bool(raw and raw.get("prompt")),
                "raw_summary_logged": bool(raw and raw.get("summary")),
            },
        }
        if raw:
            bounded = {key: bounded_raw_text(value) for key, value in raw.items() if isinstance(value, str)}
            if bounded:
                record["raw"] = bounded
        record_line = json.dumps(record, ensure_ascii=False) + "\n"
        rotate_if_needed(path, incoming_bytes=len(record_line.encode("utf-8")))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record_line)
    except Exception:
        pass


def iter_operation_file(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    records: list[dict[str, Any]] = []
    malformed = 0
    read_error = False
    if not path.is_file():
        return records, malformed, read_error
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return records, malformed, True
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            malformed += 1
    return records, malformed, read_error


def read_operation_records(*, limit: int | None = None, include_rotated: bool = False) -> list[dict[str, Any]]:
    names = [OPERATION_LOG]
    if include_rotated:
        names.append(ROTATED_OPERATION_LOG)
    records: list[dict[str, Any]] = []
    for name in names:
        file_records, _, _ = iter_operation_file(runtime_path(name))
        records.extend(file_records)
    records.sort(key=lambda record: str(record.get("timestamp", "")), reverse=True)
    if limit is not None:
        return records[:limit]
    return records


def inspect_operation_log(name: str = OPERATION_LOG) -> dict[str, Any]:
    path = runtime_path(name)
    if not path.is_file():
        return {"exists": False}
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    records, malformed, read_error = iter_operation_file(path)
    latest = ""
    raw_prompt_logged = False
    raw_summary_logged = False
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str) and timestamp > latest:
            latest = timestamp
        policy = record.get("content_policy")
        if isinstance(policy, dict):
            raw_prompt_logged = raw_prompt_logged or bool(policy.get("raw_prompt_logged"))
            raw_summary_logged = raw_summary_logged or bool(policy.get("raw_summary_logged"))
        raw = record.get("raw")
        if isinstance(raw, dict):
            raw_prompt_logged = raw_prompt_logged or isinstance(raw.get("prompt"), str)
            raw_summary_logged = raw_summary_logged or isinstance(raw.get("summary"), str)
    result: dict[str, Any] = {
        "exists": True,
        "bytes": size,
        "records": len(records),
        "malformed": malformed,
        "raw_prompt_logged": raw_prompt_logged,
        "raw_summary_logged": raw_summary_logged,
    }
    if latest:
        result["latest"] = latest
    if read_error:
        result["read_error"] = True
    return result
