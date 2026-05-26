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
    if files[ERRORS].get("records", 0) > 0:
        warnings.append("errors.log has records")
    for name, info in files.items():
        if info.get("read_error"):
            warnings.append(f"{name} has read_error")
        if info.get("malformed"):
            warnings.append(f"{name} has malformed records")
    if any(raw_content_detected(record) for record in operations):
        warnings.append("raw prompt/summary content is present in operation log")
    return {
        "runtime_dir": str(runtime_dir()),
        "status": final_status(files),
        "readiness": readiness,
        "files": files,
        "operations": operations,
        "warnings": warnings,
    }


def raw_content_detected(record: dict[str, Any]) -> bool:
    policy = record.get("content_policy")
    if isinstance(policy, dict) and (policy.get("raw_prompt_logged") or policy.get("raw_summary_logged")):
        return True
    raw = record.get("raw")
    return isinstance(raw, dict) and any(key in raw for key in ("prompt", "summary"))


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
