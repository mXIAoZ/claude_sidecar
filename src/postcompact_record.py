from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sidecar_paths import runtime_path, write_error

MAX_HISTORY_BYTES = 5_000_000
MAX_PAYLOAD_CHARS = 200_000


def read_payload() -> dict | None:
    raw = sys.stdin.read(MAX_PAYLOAD_CHARS + 1)
    if len(raw) > MAX_PAYLOAD_CHARS:
        write_error("PostCompact hook payload exceeded size limit")
        return None

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        write_error("failed to parse PostCompact hook payload", exc=exc)
        return None

    if isinstance(payload, dict):
        return payload

    write_error("PostCompact hook payload was not a JSON object")
    return None


def rotate_history_if_needed(history_path, record_line: str) -> None:
    record_bytes = len(record_line.encode("utf-8"))
    if not history_path.exists() or history_path.stat().st_size + record_bytes <= MAX_HISTORY_BYTES:
        return

    rotated_path = history_path.with_name(history_path.name + ".1")
    rotated_path.unlink(missing_ok=True)
    history_path.replace(rotated_path)


def append_history(payload: dict) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload_bytes": len(payload_json.encode("utf-8")),
        "payload": payload,
    }
    record_line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    history_path = runtime_path("compact-history.jsonl")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_history_if_needed(history_path, record_line)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(record_line)


def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    try:
        append_history(payload)
    except Exception as exc:
        write_error("failed to append compact-history.jsonl", exc=exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
