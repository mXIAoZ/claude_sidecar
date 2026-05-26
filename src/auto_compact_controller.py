from __future__ import annotations

import argparse
import sys
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_candidates import collect_recent_candidates
from merge_compact_history import DRAFT_NAME, MAX_DRAFT_SUMMARIES, build_draft
from operation_log import append_operation
from readiness import READINESS_ACCURACY, READINESS_BASIS, readiness_level
from sidecar_paths import runtime_dir, runtime_path
from status import HISTORY, compact_readiness, estimated_runtime_chars, inspect_jsonl, inspect_runtime

READINESS_ORDER = {"low": 0, "medium": 1, "high": 2, "attention": 3}
DEFAULT_WAIT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class ControllerConfig:
    pane: str | None
    dry_run: bool
    confirm_send: bool
    prompt_file: Path | None
    prompt_stdin: bool
    min_readiness: str
    wait_postcompact: bool
    wait_timeout_seconds: float
    poll_interval_seconds: float
    merge_after: bool
    tmux_path: str
    operation_log: bool
    log_raw_prompt: bool


@dataclass(frozen=True)
class PromptInfo:
    text: str
    source: str
    chars: int


@dataclass(frozen=True)
class ControllerPlan:
    runtime_level: str
    estimated_chars: int
    prompt_chars: int
    prompt_source: str
    readiness_level: str
    should_compact: bool
    actions: tuple[str, ...]


@dataclass(frozen=True)
class SendResult:
    returncode: int
    error_kind: str | None = None


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely orchestrate compact for an explicit Claude Code tmux pane.")
    parser.add_argument("--pane", help="explicit tmux target pane, for example session:window.pane")
    dry_run_group = parser.add_mutually_exclusive_group()
    dry_run_group.add_argument("--dry-run", action="store_true", dest="dry_run", default=True)
    dry_run_group.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    parser.add_argument("--confirm-send", action="store_true", help="required with --no-dry-run before sending tmux keys")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt-file", type=Path, help="explicit file containing the prompt to send after compact")
    prompt_group.add_argument("--prompt-stdin", action="store_true", help="read the prompt to send from stdin")
    parser.add_argument("--min-readiness", choices=tuple(READINESS_ORDER), default="high")
    parser.add_argument("--wait-postcompact", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=positive_float, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=positive_float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--merge-after", action="store_true")
    parser.add_argument("--tmux-path", default="tmux")
    parser.add_argument("--operation-log", action="store_true", help="append metadata-only controller operations to operation-log.jsonl")
    parser.add_argument("--log-raw-prompt", action="store_true", help="store bounded raw prompt text in operation-log.jsonl; sensitive")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ControllerConfig:
    return ControllerConfig(
        pane=args.pane,
        dry_run=args.dry_run,
        confirm_send=args.confirm_send,
        prompt_file=args.prompt_file,
        prompt_stdin=args.prompt_stdin,
        min_readiness=args.min_readiness,
        wait_postcompact=args.wait_postcompact,
        wait_timeout_seconds=args.wait_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        merge_after=args.merge_after,
        tmux_path=args.tmux_path,
        operation_log=args.operation_log,
        log_raw_prompt=args.log_raw_prompt,
    )


def read_prompt(config: ControllerConfig) -> PromptInfo:
    if config.prompt_file is not None:
        return PromptInfo(config.prompt_file.read_text(encoding="utf-8"), f"file:{config.prompt_file}", 0)
    if config.prompt_stdin:
        text = sys.stdin.read()
        return PromptInfo(text, "stdin", len(text))
    return PromptInfo("", "none", 0)


def normalize_prompt_info(prompt: PromptInfo) -> PromptInfo:
    if prompt.chars == len(prompt.text):
        return prompt
    return PromptInfo(prompt.text, prompt.source, len(prompt.text))


def prompt_source_for_output(prompt: PromptInfo) -> str:
    if prompt.source.startswith("file:"):
        return "file"
    return prompt.source


def estimate_plan(config: ControllerConfig, prompt: PromptInfo) -> ControllerPlan:
    files = inspect_runtime()
    runtime_readiness = compact_readiness(files)
    estimated_chars = estimated_runtime_chars(files) + prompt.chars
    level = readiness_level(
        estimated_chars,
        attention=runtime_readiness["level"] == "attention",
    )
    should_compact = READINESS_ORDER[level] >= READINESS_ORDER[config.min_readiness]
    actions: list[str] = []
    if should_compact:
        actions.append("send compact")
        if config.wait_postcompact:
            actions.append("wait postcompact")
        if config.merge_after:
            actions.append("write draft")
    if prompt.text:
        actions.append("send prompt")
    if not actions:
        actions.append("noop")
    return ControllerPlan(
        runtime_level=str(runtime_readiness["level"]),
        estimated_chars=estimated_chars,
        prompt_chars=prompt.chars,
        prompt_source=prompt_source_for_output(prompt),
        readiness_level=level,
        should_compact=should_compact,
        actions=tuple(actions),
    )


def render_plan(config: ControllerConfig, plan: ControllerPlan) -> str:
    lines = [
        "Auto Compact Controller",
        f"runtime_dir: {runtime_dir()}",
        f"mode: {'dry-run' if config.dry_run else 'confirmed'}",
        f"pane: {config.pane or 'none'}",
        f"runtime_readiness: {plan.runtime_level}",
        f"readiness: {plan.readiness_level}",
        f"estimated_chars={plan.estimated_chars}",
        f"prompt_source={plan.prompt_source}",
        f"prompt_chars={plan.prompt_chars}",
        f"basis={READINESS_BASIS}",
        f"accuracy={READINESS_ACCURACY}",
        f"should_compact={'yes' if plan.should_compact else 'no'}",
        "actions: " + ", ".join(plan.actions),
    ]
    if config.dry_run:
        lines.append("dry_run: no tmux commands or runtime writes performed")
    return "\n".join(lines) + "\n"


def operation_metadata(config: ControllerConfig, plan: ControllerPlan) -> dict[str, Any]:
    return {
        "dry_run": config.dry_run,
        "pane": config.pane or "",
        "runtime_readiness": plan.runtime_level,
        "readiness": plan.readiness_level,
        "estimated_chars": plan.estimated_chars,
        "prompt_source": plan.prompt_source,
        "prompt_chars": plan.prompt_chars,
        "should_compact": plan.should_compact,
        "actions": list(plan.actions),
    }


def log_controller_operation(
    config: ControllerConfig,
    operation: str,
    status: str,
    plan: ControllerPlan,
    prompt: PromptInfo,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    if not config.operation_log and not config.log_raw_prompt:
        return
    metadata = operation_metadata(config, plan)
    if extra:
        metadata.update(extra)
    raw = {"prompt": prompt.text} if config.log_raw_prompt and prompt.text else None
    append_operation(
        "auto-compact-controller",
        operation,
        status,
        metadata=metadata,
        raw=raw,
        content_policy={"raw_prompt_logged": bool(raw), "raw_summary_logged": False},
    )


def validate_send_config(config: ControllerConfig, plan: ControllerPlan) -> str | None:
    if config.log_raw_prompt and not config.operation_log:
        return "--log-raw-prompt requires --operation-log"
    if config.dry_run:
        return None
    if not config.confirm_send:
        return "--confirm-send is required with --no-dry-run"
    if (plan.should_compact or plan.prompt_chars > 0) and not config.pane:
        return "--pane is required with --no-dry-run --confirm-send"
    return None


def send_tmux_keys(tmux_path: str, pane: str, text: str) -> SendResult:
    try:
        literal = subprocess.run(
            [tmux_path, "send-keys", "-t", pane, "-l", text],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        return SendResult(1, type(exc).__name__)
    if literal.returncode != 0:
        return SendResult(literal.returncode)
    try:
        enter = subprocess.run(
            [tmux_path, "send-keys", "-t", pane, "C-m"],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        return SendResult(1, type(exc).__name__)
    return SendResult(enter.returncode)


def history_snapshot() -> dict[str, Any]:
    info = inspect_jsonl(HISTORY)
    return {
        "exists": bool(info.get("exists")),
        "bytes": int(info.get("bytes") or 0),
        "records": int(info.get("records") or 0),
        "latest": str(info.get("latest") or ""),
        "malformed": int(info.get("malformed") or 0),
        "read_error": bool(info.get("read_error")),
    }


def history_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return any(before.get(key) != after.get(key) for key in ("exists", "bytes", "records", "latest", "malformed", "read_error"))


def wait_for_postcompact_update(before: dict[str, Any], timeout_seconds: float, poll_interval_seconds: float) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    latest = history_snapshot()
    while time.monotonic() < deadline:
        latest = history_snapshot()
        if history_changed(before, latest):
            return True, latest
        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))
    return False, latest


def write_draft_from_history() -> Path:
    draft_path = runtime_path(DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        build_draft(collect_recent_candidates(limit=MAX_DRAFT_SUMMARIES, service="auto-compact-controller")),
        encoding="utf-8",
    )
    return draft_path


def run_controller(config: ControllerConfig) -> int:
    try:
        prompt = normalize_prompt_info(read_prompt(config))
    except (OSError, UnicodeError) as exc:
        print(f"failed to read prompt source: {type(exc).__name__}", file=sys.stderr)
        return 2

    plan = estimate_plan(config, prompt)
    print(render_plan(config, plan), end="")

    validation_error = validate_send_config(config, plan)
    if validation_error:
        print(validation_error, file=sys.stderr)
        log_controller_operation(config, "validate", "error", plan, prompt, extra={"error": validation_error})
        return 2
    if config.dry_run:
        log_controller_operation(config, "dry-run", "ok", plan, prompt)
        return 0

    compact_sent = False
    before_history = history_snapshot() if plan.should_compact and config.wait_postcompact else None
    if plan.should_compact:
        result = send_tmux_keys(config.tmux_path, config.pane or "", "/compact")
        if result.returncode != 0:
            print(f"tmux compact send failed: returncode={result.returncode}, error_kind={result.error_kind or 'none'}", file=sys.stderr)
            log_controller_operation(
                config,
                "send-compact",
                "error",
                plan,
                prompt,
                extra={"tmux_returncode": result.returncode, "error_kind": result.error_kind or "none"},
            )
            return 1
        compact_sent = True
        log_controller_operation(config, "send-compact", "ok", plan, prompt, extra={"tmux_returncode": result.returncode})
        print("sent_compact=yes")

    if compact_sent and before_history is not None:
        changed, after_history = wait_for_postcompact_update(
            before_history,
            config.wait_timeout_seconds,
            config.poll_interval_seconds,
        )
        print(
            "postcompact_changed="
            + ("yes" if changed else "no")
            + f", records_before={before_history['records']}, records_after={after_history['records']}, bytes_before={before_history['bytes']}, bytes_after={after_history['bytes']}"
        )
        log_controller_operation(
            config,
            "wait-postcompact",
            "ok" if changed else "timeout",
            plan,
            prompt,
            extra={
                "records_before": before_history["records"],
                "records_after": after_history["records"],
                "bytes_before": before_history["bytes"],
                "bytes_after": after_history["bytes"],
            },
        )
        if not changed:
            return 3

    if compact_sent and config.merge_after:
        draft_path = write_draft_from_history()
        log_controller_operation(config, "write-draft", "ok", plan, prompt, extra={"draft_path": str(draft_path)})
        print(f"draft_written={draft_path}")

    if prompt.text:
        result = send_tmux_keys(config.tmux_path, config.pane or "", prompt.text)
        if result.returncode != 0:
            print(f"tmux prompt send failed: returncode={result.returncode}, error_kind={result.error_kind or 'none'}", file=sys.stderr)
            log_controller_operation(
                config,
                "send-prompt",
                "error",
                plan,
                prompt,
                extra={"tmux_returncode": result.returncode, "error_kind": result.error_kind or "none"},
            )
            return 1
        log_controller_operation(config, "send-prompt", "ok", plan, prompt, extra={"tmux_returncode": result.returncode})
        print("sent_prompt=yes")
    elif not compact_sent:
        log_controller_operation(config, "noop", "ok", plan, prompt)
        print("noop: readiness below threshold and no prompt source")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_controller(config_from_args(parse_args(argv or sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
