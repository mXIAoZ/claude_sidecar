from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from config import CONFIG_PATH_ENV, SidecarConfigError, load_config, load_config_for_import, print_config_error
from paths import runtime_dir, runtime_path
from compact_sidecar.runtime import operation_log
from compact_sidecar.runtime import readiness
from compact_sidecar.runtime import summary_context

_CONFIG = load_config_for_import()
_RUNTIME_FILES = _CONFIG["paths"]["runtime_files"]
ROLLING_SUMMARY = str(_RUNTIME_FILES["rolling_summary"])
DRAFT = str(_RUNTIME_FILES["rolling_summary_draft"])
HISTORY = str(_RUNTIME_FILES["compact_history"])
ROTATED_HISTORY = str(_RUNTIME_FILES["compact_history_rotated"])
ERRORS = str(_RUNTIME_FILES["errors"])
DAEMON_STATE = str(_RUNTIME_FILES["daemon_state"])
KNOWN_FILES = tuple[str, ...]()


def refresh_config(config_path: str | None = None, *, strict: bool = False) -> None:
    global _CONFIG, _RUNTIME_FILES, ROLLING_SUMMARY, DRAFT, HISTORY, ROTATED_HISTORY, ERRORS, DAEMON_STATE, KNOWN_FILES

    _CONFIG = load_config(config_path) if strict or config_path else load_config_for_import()
    operation_log.refresh_config(config_path, strict=strict)
    readiness.refresh_config(config_path, strict=strict)
    summary_context.refresh_config(config_path, strict=strict)
    _RUNTIME_FILES = _CONFIG["paths"]["runtime_files"]
    ROLLING_SUMMARY = str(_RUNTIME_FILES["rolling_summary"])
    DRAFT = str(_RUNTIME_FILES["rolling_summary_draft"])
    HISTORY = str(_RUNTIME_FILES["compact_history"])
    ROTATED_HISTORY = str(_RUNTIME_FILES["compact_history_rotated"])
    ERRORS = str(_RUNTIME_FILES["errors"])
    DAEMON_STATE = str(_RUNTIME_FILES["daemon_state"])
    KNOWN_FILES = tuple(
        dict.fromkeys(
            [
                ROLLING_SUMMARY,
                HISTORY,
                ROTATED_HISTORY,
                DRAFT,
                operation_log.OPERATION_LOG,
                operation_log.ROTATED_OPERATION_LOG,
                DAEMON_STATE,
                ERRORS,
                *[str(name) for name in _CONFIG["dashboard_status"]["known_files_order"]],
            ]
        )
    )


refresh_config()


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

    marker_present = summary_context.INJECTION_MARKER in text
    inject_always = os.environ.get(summary_context.INJECT_ALWAYS_ENV) == "1" or bool(_CONFIG["summary"].get("inject_always"))
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
    llm_summary_status = state.get("llm_summary_status")
    llm_summary_skipped = state.get("llm_summary_skipped")
    llm_provider = state.get("llm_provider")
    llm_model = state.get("llm_model")
    summary_written = state.get("summary_written")
    summary_backup = state.get("summary_backup")
    llm_prompt_tokens = state.get("llm_prompt_tokens")
    llm_completion_tokens = state.get("llm_completion_tokens")
    llm_total_tokens = state.get("llm_total_tokens")
    llm_elapsed_ms = state.get("llm_elapsed_ms")
    llm_last_success_model = state.get("llm_last_success_model")
    llm_last_success_prompt_tokens = state.get("llm_last_success_prompt_tokens")
    llm_last_success_completion_tokens = state.get("llm_last_success_completion_tokens")
    llm_last_success_total_tokens = state.get("llm_last_success_total_tokens")
    llm_last_success_elapsed_ms = state.get("llm_last_success_elapsed_ms")
    if isinstance(llm_summary_status, str):
        result["llm_summary_status"] = llm_summary_status
    if isinstance(llm_summary_skipped, str):
        result["llm_summary_skipped"] = llm_summary_skipped
    if isinstance(llm_provider, str):
        result["llm_provider"] = llm_provider
    if isinstance(llm_model, str):
        result["llm_model"] = llm_model
    if isinstance(summary_written, str):
        result["summary_written"] = summary_written
    if isinstance(summary_backup, str):
        result["summary_backup"] = summary_backup
    if isinstance(llm_prompt_tokens, int) or (llm_prompt_tokens is None and "llm_prompt_tokens" in state):
        result["llm_prompt_tokens"] = llm_prompt_tokens
    if isinstance(llm_completion_tokens, int) or (llm_completion_tokens is None and "llm_completion_tokens" in state):
        result["llm_completion_tokens"] = llm_completion_tokens
    if isinstance(llm_total_tokens, int) or (llm_total_tokens is None and "llm_total_tokens" in state):
        result["llm_total_tokens"] = llm_total_tokens
    if isinstance(llm_elapsed_ms, int):
        result["llm_elapsed_ms"] = llm_elapsed_ms
    if isinstance(llm_last_success_model, str):
        result["llm_last_success_model"] = llm_last_success_model
    if isinstance(llm_last_success_prompt_tokens, int) or (llm_last_success_prompt_tokens is None and "llm_last_success_prompt_tokens" in state):
        result["llm_last_success_prompt_tokens"] = llm_last_success_prompt_tokens
    if isinstance(llm_last_success_completion_tokens, int) or (llm_last_success_completion_tokens is None and "llm_last_success_completion_tokens" in state):
        result["llm_last_success_completion_tokens"] = llm_last_success_completion_tokens
    if isinstance(llm_last_success_total_tokens, int) or (llm_last_success_total_tokens is None and "llm_last_success_total_tokens" in state):
        result["llm_last_success_total_tokens"] = llm_last_success_total_tokens
    if isinstance(llm_last_success_elapsed_ms, int):
        result["llm_last_success_elapsed_ms"] = llm_last_success_elapsed_ms
    if isinstance(error_kind, str):
        result["error_kind"] = error_kind
    return result

def inspect_runtime() -> dict[str, dict[str, Any]]:
    return {
        ROLLING_SUMMARY: inspect_summary(),
        DRAFT: inspect_file_metadata(DRAFT),
        HISTORY: inspect_jsonl(HISTORY),
        ROTATED_HISTORY: inspect_jsonl(ROTATED_HISTORY),
        operation_log.OPERATION_LOG: operation_log.inspect_operation_log(operation_log.OPERATION_LOG),
        operation_log.ROTATED_OPERATION_LOG: operation_log.inspect_operation_log(operation_log.ROTATED_OPERATION_LOG),
        ERRORS: inspect_jsonl(ERRORS),
        DAEMON_STATE: inspect_daemon_state(),
    }


def daemon_llm_error(files: dict[str, dict[str, Any]]) -> bool:
    return files[DAEMON_STATE].get("llm_summary_status") == "error"


def final_status(files: dict[str, dict[str, Any]]) -> str:
    if any(info.get("read_error") or info.get("malformed") for info in files.values()):
        return "attention"
    if daemon_llm_error(files):
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
    level = readiness.readiness_level(
        estimated_chars,
        attention=any(info.get("read_error") or info.get("malformed") for info in files.values()),
    )
    return {
        "level": level,
        "estimated_chars": estimated_chars,
        "basis": readiness.READINESS_BASIS,
        "accuracy": readiness.READINESS_ACCURACY,
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
    elif name in (HISTORY, ROTATED_HISTORY, ERRORS, operation_log.OPERATION_LOG, operation_log.ROTATED_OPERATION_LOG):
        parts.append(f"records={info.get('records', 0)}")
        if info.get("latest"):
            parts.append(f"latest={info['latest']}")
        if info.get("malformed"):
            parts.append(f"malformed={info['malformed']}")
        if name in (operation_log.OPERATION_LOG, operation_log.ROTATED_OPERATION_LOG):
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
        if info.get("llm_summary_status"):
            parts.append(f"llm_summary_status={info['llm_summary_status']}")
        if info.get("llm_summary_skipped"):
            parts.append(f"llm_summary_skipped={info['llm_summary_skipped']}")
        if info.get("llm_provider"):
            parts.append(f"llm_provider={info['llm_provider']}")
        if info.get("llm_model"):
            parts.append(f"llm_model={info['llm_model']}")
        if "llm_prompt_tokens" in info:
            parts.append(f"llm_prompt_tokens={info['llm_prompt_tokens'] if info['llm_prompt_tokens'] is not None else 'unknown'}")
        if "llm_completion_tokens" in info:
            parts.append(f"llm_completion_tokens={info['llm_completion_tokens'] if info['llm_completion_tokens'] is not None else 'unknown'}")
        if "llm_total_tokens" in info:
            parts.append(f"llm_total_tokens={info['llm_total_tokens'] if info['llm_total_tokens'] is not None else 'unknown'}")
        if "llm_elapsed_ms" in info:
            parts.append(f"llm_elapsed_ms={info['llm_elapsed_ms']}")
        if info.get("llm_last_success_model"):
            parts.append(f"llm_last_success_model={info['llm_last_success_model']}")
        if "llm_last_success_prompt_tokens" in info:
            parts.append(f"llm_last_success_prompt_tokens={info['llm_last_success_prompt_tokens'] if info['llm_last_success_prompt_tokens'] is not None else 'unknown'}")
        if "llm_last_success_completion_tokens" in info:
            parts.append(f"llm_last_success_completion_tokens={info['llm_last_success_completion_tokens'] if info['llm_last_success_completion_tokens'] is not None else 'unknown'}")
        if "llm_last_success_total_tokens" in info:
            parts.append(f"llm_last_success_total_tokens={info['llm_last_success_total_tokens'] if info['llm_last_success_total_tokens'] is not None else 'unknown'}")
        if "llm_last_success_elapsed_ms" in info:
            parts.append(f"llm_last_success_elapsed_ms={info['llm_last_success_elapsed_ms']}")
        if info.get("summary_written"):
            parts.append(f"summary_written={info['summary_written']}")
        if info.get("summary_backup"):
            parts.append(f"summary_backup={info['summary_backup']}")
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
    lines.extend(render_file_line(name, files[name]) for name in KNOWN_FILES if name in files)
    lines.extend(["", render_readiness_line(files), f"status: {final_status(files)}"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report read-only sidecar compact runtime status.")
    parser.add_argument("--config", help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.")
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    active_config_path = args.config or os.environ.get(CONFIG_PATH_ENV, "").strip() or None
    if args.config:
        os.environ[CONFIG_PATH_ENV] = args.config
    try:
        refresh_config(active_config_path, strict=active_config_path is not None)
    except SidecarConfigError as exc:
        print_config_error("compact_sidecar.ui.status", exc)
        return 1
    print(render_status(inspect_runtime()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
