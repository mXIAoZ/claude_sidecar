from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sidecar_paths import runtime_dir, runtime_path
from summary_context import INJECT_ALWAYS_ENV, INJECTION_MARKER

ROLLING_SUMMARY = "rolling-summary.md"
DRAFT = "rolling-summary.draft.md"
HISTORY = "compact-history.jsonl"
ROTATED_HISTORY = "compact-history.jsonl.1"
ERRORS = "errors.log"
KNOWN_FILES = (ROLLING_SUMMARY, DRAFT, HISTORY, ROTATED_HISTORY, ERRORS)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def inspect_file_metadata(name: str) -> dict[str, Any]:
    path = runtime_path(name)
    if not path.is_file():
        return {"exists": False}
    return {"exists": True, "bytes": file_size(path)}


def inspect_summary() -> dict[str, Any]:
    path = runtime_path(ROLLING_SUMMARY)
    if not path.is_file():
        return {"exists": False}

    result: dict[str, Any] = {"exists": True, "bytes": file_size(path)}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        result["read_error"] = str(exc)
        return result

    marker_present = INJECTION_MARKER in text
    inject_always = os.environ.get(INJECT_ALWAYS_ENV) == "1"
    result["chars"] = len(text)
    result["non_empty"] = bool(text.strip())
    result["marker_present"] = marker_present
    result["injectable"] = result["non_empty"] and (marker_present or inject_always)
    return result


def inspect_jsonl(name: str) -> dict[str, Any]:
    path = runtime_path(name)
    if not path.is_file():
        return {"exists": False}

    result: dict[str, Any] = {"exists": True, "bytes": file_size(path), "records": 0, "malformed": 0}
    latest = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        result["read_error"] = str(exc)
        return result

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            result["malformed"] += 1
            continue
        result["records"] += 1
        if isinstance(record, dict):
            timestamp = record.get("timestamp")
            if isinstance(timestamp, str) and timestamp > latest:
                latest = timestamp
    if latest:
        result["latest"] = latest
    return result


def inspect_runtime() -> dict[str, dict[str, Any]]:
    return {
        ROLLING_SUMMARY: inspect_summary(),
        DRAFT: inspect_file_metadata(DRAFT),
        HISTORY: inspect_jsonl(HISTORY),
        ROTATED_HISTORY: inspect_jsonl(ROTATED_HISTORY),
        ERRORS: inspect_jsonl(ERRORS),
    }


def final_status(files: dict[str, dict[str, Any]]) -> str:
    if any(info.get("read_error") or info.get("malformed") for info in files.values()):
        return "attention"
    errors = files[ERRORS]
    if errors.get("records", 0) > 0:
        return "attention"
    if files[ROLLING_SUMMARY].get("injectable"):
        return "ready"
    if any(info.get("exists") for info in files.values()):
        return "inactive"
    return "empty"


def yes_no(value: object) -> str:
    return "yes" if value else "no"


def render_file_line(name: str, info: dict[str, Any]) -> str:
    if not info.get("exists"):
        return f"{name}: absent"

    parts = [f"{name}: present", f"bytes={info.get('bytes', 0)}"]
    if name == ROLLING_SUMMARY:
        parts.extend(
            [
                f"chars={info.get('chars', 0)}",
                f"marker={yes_no(info.get('marker_present'))}",
                f"injectable={yes_no(info.get('injectable'))}",
            ]
        )
    elif name in (HISTORY, ROTATED_HISTORY, ERRORS):
        parts.append(f"records={info.get('records', 0)}")
        if info.get("latest"):
            parts.append(f"latest={info['latest']}")
        if info.get("malformed"):
            parts.append(f"malformed={info['malformed']}")
    elif name == DRAFT:
        pass
    if info.get("read_error"):
        parts.append("read_error=yes")
    return ", ".join(parts)


def render_status(files: dict[str, dict[str, Any]]) -> str:
    lines = ["Sidecar Compact Status", f"runtime_dir: {runtime_dir()}", ""]
    lines.extend(render_file_line(name, files[name]) for name in KNOWN_FILES)
    lines.extend(["", f"status: {final_status(files)}"])
    return "\n".join(lines) + "\n"


def main() -> int:
    print(render_status(inspect_runtime()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
