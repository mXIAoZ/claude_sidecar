from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sidecar_paths import runtime_dir, runtime_path
from summary_context import INJECT_ALWAYS_ENV, INJECTION_MARKER
from readiness import READINESS_ACCURACY, READINESS_BASIS, readiness_level
from operation_log import OPERATION_LOG, ROTATED_OPERATION_LOG, inspect_operation_log

ROLLING_SUMMARY = "rolling-summary.md"
DRAFT = "rolling-summary.draft.md"
HISTORY = "compact-history.jsonl"
ROTATED_HISTORY = "compact-history.jsonl.1"
ERRORS = "errors.log"
DAEMON_STATE = "daemon-state.json"
KNOWN_FILES = (ROLLING_SUMMARY, DRAFT, HISTORY, ROTATED_HISTORY, OPERATION_LOG, ROTATED_OPERATION_LOG, ERRORS, DAEMON_STATE)


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


def inspect_daemon_state() -> dict[str, Any]:
    path = runtime_path(DAEMON_STATE)
    if not path.is_file():
        return {"exists": False}

    result: dict[str, Any] = {"exists": True, "bytes": file_size(path)}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        result["malformed"] = True
        return result
    except (OSError, UnicodeError) as exc:
        result["read_error"] = str(exc)
        return result

    if not isinstance(state, dict):
        result["malformed"] = True
        return result

    mode = state.get("mode")
    timestamp = state.get("timestamp")
    candidate_count = state.get("candidate_count")
    interval_seconds = state.get("interval_seconds")
    run_count = state.get("run_count")
    shutdown_reason = state.get("shutdown_reason")
    plist_path = state.get("plist_path")
    label = state.get("label")
    plist_removed = state.get("plist_removed")
    launchctl_invoked = state.get("launchctl_invoked")
    launchctl_action = state.get("launchctl_action")
    launchctl_target = state.get("launchctl_target")
    launchctl_returncode = state.get("launchctl_returncode")
    launchctl_status = state.get("launchctl_status")
    plist_validated = state.get("plist_validated")
    error_kind = state.get("error_kind")
    if isinstance(mode, str):
        result["mode"] = mode
    if isinstance(timestamp, str):
        result["last_run"] = timestamp
    if isinstance(candidate_count, int):
        result["candidate_count"] = candidate_count
    if isinstance(interval_seconds, int):
        result["interval_seconds"] = interval_seconds
    if isinstance(run_count, int):
        result["run_count"] = run_count
    if isinstance(shutdown_reason, str):
        result["shutdown_reason"] = shutdown_reason
    if isinstance(plist_path, str):
        result["plist_path"] = plist_path
    if isinstance(label, str):
        result["label"] = label
    if isinstance(plist_removed, bool):
        result["plist_removed"] = plist_removed
    if isinstance(launchctl_invoked, bool):
        result["launchctl_invoked"] = launchctl_invoked
    if isinstance(launchctl_action, str):
        result["launchctl_action"] = launchctl_action
    if isinstance(launchctl_target, str):
        result["launchctl_target"] = launchctl_target
    if isinstance(launchctl_returncode, int):
        result["launchctl_returncode"] = launchctl_returncode
    if isinstance(launchctl_status, str):
        result["launchctl_status"] = launchctl_status
    if isinstance(plist_validated, bool):
        result["plist_validated"] = plist_validated
    if isinstance(error_kind, str):
        result["error_kind"] = error_kind
    return result


def inspect_runtime() -> dict[str, dict[str, Any]]:
    return {
        ROLLING_SUMMARY: inspect_summary(),
        DRAFT: inspect_file_metadata(DRAFT),
        HISTORY: inspect_jsonl(HISTORY),
        ROTATED_HISTORY: inspect_jsonl(ROTATED_HISTORY),
        OPERATION_LOG: inspect_operation_log(OPERATION_LOG),
        ROTATED_OPERATION_LOG: inspect_operation_log(ROTATED_OPERATION_LOG),
        ERRORS: inspect_jsonl(ERRORS),
        DAEMON_STATE: inspect_daemon_state(),
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


def estimated_runtime_chars(files: dict[str, dict[str, Any]]) -> int:
    summary = files[ROLLING_SUMMARY]
    total = int(summary.get("chars") or summary.get("bytes") or 0)
    for name in (DRAFT, HISTORY, ROTATED_HISTORY, DAEMON_STATE):
        total += int(files[name].get("bytes") or 0)
    return total


def compact_readiness(files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    estimated_chars = estimated_runtime_chars(files)
    level = readiness_level(
        estimated_chars,
        attention=any(info.get("read_error") or info.get("malformed") for info in files.values()),
    )
    return {
        "level": level,
        "estimated_chars": estimated_chars,
        "basis": READINESS_BASIS,
        "accuracy": READINESS_ACCURACY,
    }


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
    elif name in (HISTORY, ROTATED_HISTORY, ERRORS, OPERATION_LOG, ROTATED_OPERATION_LOG):
        parts.append(f"records={info.get('records', 0)}")
        if info.get("latest"):
            parts.append(f"latest={info['latest']}")
        if info.get("malformed"):
            parts.append(f"malformed={info['malformed']}")
        if name in (OPERATION_LOG, ROTATED_OPERATION_LOG):
            if "raw_prompt_logged" in info:
                parts.append(f"raw_prompt_logged={yes_no(info['raw_prompt_logged'])}")
            if "raw_summary_logged" in info:
                parts.append(f"raw_summary_logged={yes_no(info['raw_summary_logged'])}")
    elif name == DAEMON_STATE:
        if info.get("mode"):
            parts.append(f"mode={info['mode']}")
        if info.get("last_run"):
            parts.append(f"last_run={info['last_run']}")
        if "candidate_count" in info:
            parts.append(f"candidate_count={info['candidate_count']}")
        if "interval_seconds" in info:
            parts.append(f"interval_seconds={info['interval_seconds']}")
        if "run_count" in info:
            parts.append(f"run_count={info['run_count']}")
        if info.get("shutdown_reason"):
            parts.append(f"shutdown_reason={info['shutdown_reason']}")
        if info.get("plist_path"):
            parts.append(f"plist_path={info['plist_path']}")
        if info.get("label"):
            parts.append(f"label={info['label']}")
        if "plist_removed" in info:
            parts.append(f"plist_removed={yes_no(info['plist_removed'])}")
        if "launchctl_invoked" in info:
            parts.append(f"launchctl_invoked={yes_no(info['launchctl_invoked'])}")
        if info.get("launchctl_action"):
            parts.append(f"launchctl_action={info['launchctl_action']}")
        if info.get("launchctl_target"):
            parts.append(f"launchctl_target={info['launchctl_target']}")
        if "launchctl_returncode" in info:
            parts.append(f"launchctl_returncode={info['launchctl_returncode']}")
        if info.get("launchctl_status"):
            parts.append(f"launchctl_status={info['launchctl_status']}")
        if "plist_validated" in info:
            parts.append(f"plist_validated={yes_no(info['plist_validated'])}")
        if info.get("error_kind"):
            parts.append(f"error_kind={info['error_kind']}")
        if info.get("malformed"):
            parts.append("malformed=yes")
    if info.get("read_error"):
        parts.append("read_error=yes")
    return ", ".join(parts)


def render_readiness_line(files: dict[str, dict[str, Any]]) -> str:
    readiness = compact_readiness(files)
    return ", ".join(
        [
            f"compact-readiness: {readiness['level']}",
            f"estimated_chars={readiness['estimated_chars']}",
            f"basis={readiness['basis']}",
            f"accuracy={readiness['accuracy']}",
        ]
    )


def render_status(files: dict[str, dict[str, Any]]) -> str:
    lines = ["Sidecar Compact Status", f"runtime_dir: {runtime_dir()}", ""]
    lines.extend(render_file_line(name, files[name]) for name in KNOWN_FILES)
    lines.extend(["", render_readiness_line(files), f"status: {final_status(files)}"])
    return "\n".join(lines) + "\n"


def main() -> int:
    print(render_status(inspect_runtime()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
