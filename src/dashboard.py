from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from typing import Any

from operation_log import OPERATION_LOG, ROTATED_OPERATION_LOG, read_operation_records
from readiness import READINESS_ACCURACY, READINESS_BASIS
from sidecar_paths import runtime_dir
from status import (
    DAEMON_STATE,
    DRAFT,
    ERRORS,
    HISTORY,
    ROLLING_SUMMARY,
    ROTATED_HISTORY,
    compact_readiness,
    daemon_llm_error,
    final_status,
    inspect_runtime,
    render_file_line,
)

DEFAULT_LOG_LIMIT = 20
DEFAULT_INTERVAL_SECONDS = 2.0


class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def status(self, value: str) -> str:
        colors = {"ready": "32", "low": "32", "medium": "33", "high": "31", "attention": "31", "empty": "36", "inactive": "33", "ok": "32", "error": "31"}
        return self.paint(value, colors.get(value, "0"))


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_dashboard_snapshot(log_limit: int = DEFAULT_LOG_LIMIT) -> dict[str, Any]:
    files = inspect_runtime()
    readiness = compact_readiness(files)
    operations = read_operation_records(limit=log_limit, include_rotated=True)
    warnings: list[str] = []
    if final_status(files) == "attention":
        warnings.append("runtime needs attention")
    if readiness["level"] in ("high", "attention"):
        warnings.append(f"compact readiness is {readiness['level']}")
    if daemon_llm_error(files):
        warnings.append("daemon LLM summary failed")
    if files[ERRORS].get("records", 0) > 0:
        warnings.append("errors.log has records")
    for name, info in files.items():
        if info.get("read_error"):
            warnings.append(f"{name} has read_error")
        if info.get("malformed"):
            warnings.append(f"{name} has malformed records")
    if any(raw_content_detected(record) for record in operations):
        warnings.append("raw prompt/summary content is present in operation log")
    llm_summary = latest_llm_summary(files, operations)
    return {
        "runtime_dir": str(runtime_dir()),
        "status": final_status(files),
        "readiness": readiness,
        "files": files,
        "operations": operations,
        "llm_summary": llm_summary,
        "warnings": warnings,
    }


def raw_content_detected(record: dict[str, Any]) -> bool:
    policy = record.get("content_policy")
    if isinstance(policy, dict) and (policy.get("raw_prompt_logged") or policy.get("raw_summary_logged")):
        return True
    raw = record.get("raw")
    return isinstance(raw, dict) and any(key in raw for key in ("prompt", "summary"))


def llm_summary_from_operation(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "timestamp": record.get("timestamp"),
        "status": record.get("status", "unknown"),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "prompt_tokens": metadata.get("prompt_tokens"),
        "completion_tokens": metadata.get("completion_tokens"),
        "total_tokens": metadata.get("total_tokens"),
        "elapsed_ms": metadata.get("elapsed_ms"),
        "last_success_prompt_tokens": metadata.get("last_success_prompt_tokens"),
        "last_success_completion_tokens": metadata.get("last_success_completion_tokens"),
        "last_success_total_tokens": metadata.get("last_success_total_tokens"),
        "last_success_elapsed_ms": metadata.get("last_success_elapsed_ms"),
        "summary_written": metadata.get("summary_written"),
        "summary_backup": metadata.get("summary_backup"),
        "error_kind": metadata.get("error_kind"),
    }


def llm_summary_from_state(daemon_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": daemon_state.get("last_run"),
        "status": daemon_state.get("llm_summary_status"),
        "provider": daemon_state.get("llm_provider"),
        "model": daemon_state.get("llm_model"),
        "prompt_tokens": daemon_state.get("llm_prompt_tokens"),
        "completion_tokens": daemon_state.get("llm_completion_tokens"),
        "total_tokens": daemon_state.get("llm_total_tokens"),
        "elapsed_ms": daemon_state.get("llm_elapsed_ms"),
        "last_success_prompt_tokens": daemon_state.get("llm_last_success_prompt_tokens"),
        "last_success_completion_tokens": daemon_state.get("llm_last_success_completion_tokens"),
        "last_success_total_tokens": daemon_state.get("llm_last_success_total_tokens"),
        "last_success_elapsed_ms": daemon_state.get("llm_last_success_elapsed_ms"),
        "summary_written": daemon_state.get("summary_written"),
        "summary_backup": daemon_state.get("summary_backup"),
        "error_kind": daemon_state.get("error_kind"),
    }


def summary_timestamp(summary: dict[str, Any]) -> str:
    timestamp = summary.get("timestamp")
    return timestamp if isinstance(timestamp, str) else ""


def latest_llm_summary(files: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any] | None:
    summaries: list[dict[str, Any]] = []
    for record in operations:
        if record.get("service") == "daemon" and record.get("operation") == "llm-summary":
            summaries.append(llm_summary_from_operation(record))

    daemon_state = files.get(DAEMON_STATE)
    if isinstance(daemon_state, dict) and "llm_summary_status" in daemon_state:
        summaries.append(llm_summary_from_state(daemon_state))
    if not summaries:
        return None
    return max(summaries, key=summary_timestamp)


def format_metadata(metadata: Any, *, width: int = 80) -> str:
    if not isinstance(metadata, dict) or not metadata:
        return "-"
    chunks = [f"{key}={metadata[key]}" for key in sorted(metadata)]
    text = ", ".join(chunks)
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def render_operation(record: dict[str, Any], *, show_content: bool, width: int) -> list[str]:
    timestamp = str(record.get("timestamp", "unknown"))
    service = str(record.get("service", "unknown"))
    operation = str(record.get("operation", "unknown"))
    status = str(record.get("status", "unknown"))
    lines = [
        f"{timestamp} | {service} | {operation} | {status}",
        f"  metadata: {format_metadata(record.get('metadata'), width=max(20, width - 14))}",
    ]
    if show_content:
        raw = record.get("raw")
        if isinstance(raw, dict):
            if isinstance(raw.get("prompt"), str):
                lines.append(f"  raw.prompt: {raw['prompt']}")
            if isinstance(raw.get("summary"), str):
                lines.append(f"  raw.summary: {raw['summary']}")
    elif raw_content_detected(record):
        lines.append("  raw: hidden (use --show-content to display)")
    return lines


def token_text(value: Any) -> str:
    return "unknown" if value is None else str(value)


def render_llm_summary(summary: dict[str, Any] | None) -> list[str]:
    lines = ["", "LLM Summary", "-----------"]
    if not summary:
        lines.append("No LLM summary metadata found.")
        return lines
    parts = [
        f"status={summary.get('status', 'unknown')}",
        f"provider={summary.get('provider') or 'unknown'}",
        f"model={summary.get('model') or 'unknown'}",
        f"prompt_tokens={token_text(summary.get('prompt_tokens'))}",
        f"completion_tokens={token_text(summary.get('completion_tokens'))}",
        f"total_tokens={token_text(summary.get('total_tokens'))}",
        f"elapsed_ms={token_text(summary.get('elapsed_ms'))}",
    ]
    if "last_success_total_tokens" in summary:
        parts.append(f"last_success_total_tokens={token_text(summary.get('last_success_total_tokens'))}")
    if "last_success_elapsed_ms" in summary:
        parts.append(f"last_success_elapsed_ms={token_text(summary.get('last_success_elapsed_ms'))}")
    if summary.get("summary_written"):
        parts.append(f"summary_written={summary['summary_written']}")
    if summary.get("summary_backup"):
        parts.append(f"summary_backup={summary['summary_backup']}")
    if summary.get("error_kind"):
        parts.append(f"error_kind={summary['error_kind']}")
    lines.append(", ".join(parts))
    return lines


def render_dashboard(snapshot: dict[str, Any], *, color: bool = False, width: int = 100, show_content: bool = False) -> str:
    palette = Palette(color)
    readiness = snapshot["readiness"]
    files = snapshot["files"]
    lines = [
        "Sidecar Operations Dashboard",
        "=" * min(width, 32),
        f"runtime_dir: {snapshot['runtime_dir']}",
        f"status: {palette.status(snapshot['status'])}",
        f"compact-readiness: {palette.status(readiness['level'])}, estimated_chars={readiness['estimated_chars']}, basis={READINESS_BASIS}, accuracy={READINESS_ACCURACY}",
        "",
        "Runtime Files",
        "-------------",
    ]
    for name in (ROLLING_SUMMARY, DRAFT, HISTORY, ROTATED_HISTORY, OPERATION_LOG, ROTATED_OPERATION_LOG, ERRORS, DAEMON_STATE):
        if name in files:
            lines.append(render_file_line(name, files[name]))
    lines.extend(render_llm_summary(snapshot.get("llm_summary")))
    lines.extend(["", "Recent Operations", "-----------------"])
    operations = snapshot.get("operations", [])
    if not operations:
        lines.append("No operation records found.")
    else:
        for record in operations:
            lines.extend(render_operation(record, show_content=show_content, width=width))
    lines.extend(["", "Health", "------"])
    warnings = snapshot.get("warnings", [])
    if warnings:
        lines.extend(f"! {warning}" for warning in warnings)
    else:
        lines.append("No warnings.")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a terminal dashboard for sidecar compact operations.")
    parser.add_argument("--watch", action="store_true", help="refresh until interrupted")
    parser.add_argument("--interval-seconds", type=positive_float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable dashboard snapshot")
    parser.add_argument("--log-limit", type=positive_int, default=DEFAULT_LOG_LIMIT)
    parser.add_argument("--show-content", action="store_true", help="show raw prompt/summary content if it was explicitly logged")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args(argv)


def snapshot_for_output(snapshot: dict[str, Any], *, show_content: bool) -> dict[str, Any]:
    if show_content:
        return snapshot
    sanitized = dict(snapshot)
    sanitized["operations"] = [
        {key: value for key, value in record.items() if key != "raw"}
        for record in snapshot.get("operations", [])
        if isinstance(record, dict)
    ]
    return sanitized


def render_once(args: argparse.Namespace) -> str:
    snapshot = build_dashboard_snapshot(log_limit=args.log_limit)
    if args.json:
        return json.dumps(snapshot_for_output(snapshot, show_content=args.show_content), ensure_ascii=False, sort_keys=True) + "\n"
    terminal = shutil.get_terminal_size(fallback=(100, 30))
    color = (not args.no_color) and sys.stdout.isatty()
    return render_dashboard(snapshot, color=color, width=terminal.columns, show_content=args.show_content)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.watch:
        print(render_once(args), end="")
        return 0
    try:
        while True:
            print("\033[2J\033[H", end="")
            print(render_once(args), end="")
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
