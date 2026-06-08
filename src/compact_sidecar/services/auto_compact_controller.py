from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compact_sidecar.runtime import merge_compact_history
from compact_sidecar.runtime import readiness
from compact_sidecar.runtime import rolling_summary_writer
from compact_sidecar.ui import status
from compact_sidecar.runtime.memory_candidates import MemoryCandidate, collect_recent_candidates
from compact_sidecar.runtime.operation_log import append_operation
from compact_sidecar.runtime.rolling_summary_writer import write_rolling_summary_with_backup
from compact_sidecar.config import CONFIG_PATH_ENV, cli_config_path, load_config_for_import, load_config_safe
from compact_sidecar.paths import runtime_dir

_CONFIG = load_config_for_import()
_CONTROLLER_CONFIG = _CONFIG["controller"]
READINESS_ORDER = {level: index for index, level in enumerate(_CONFIG["readiness"]["levels"])}
DEFAULT_WAIT_TIMEOUT_SECONDS = float(_CONTROLLER_CONFIG["wait_timeout_seconds"])
DEFAULT_POLL_INTERVAL_SECONDS = float(_CONTROLLER_CONFIG["poll_interval_seconds"])
DEFAULT_TMUX_PATH = str(_CONTROLLER_CONFIG["tmux_path"])
DEFAULT_MIN_READINESS = str(_CONTROLLER_CONFIG["min_readiness"])
DEFAULT_PANE = str(_CONTROLLER_CONFIG.get("pane") or "") or None
DEFAULT_NO_SEND = bool(_CONTROLLER_CONFIG["no_send"])
DEFAULT_WAIT_POSTCOMPACT = bool(_CONTROLLER_CONFIG["wait_postcompact"])
DEFAULT_MERGE_AFTER = bool(_CONTROLLER_CONFIG["merge_after"])
DEFAULT_OPERATION_LOG = bool(_CONTROLLER_CONFIG["operation_log"])
DEFAULT_LOG_RAW_PROMPT = bool(_CONTROLLER_CONFIG["log_raw_prompt"])
COMPACT_COMMAND = str(_CONTROLLER_CONFIG["compact_command"])
ENTER_KEY = str(_CONTROLLER_CONFIG["enter_key"])


def refresh_config(config_path: str | None = None) -> None:
    global _CONFIG, _CONTROLLER_CONFIG, READINESS_ORDER
    global DEFAULT_WAIT_TIMEOUT_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS, DEFAULT_TMUX_PATH, DEFAULT_MIN_READINESS
    global DEFAULT_PANE, DEFAULT_NO_SEND, DEFAULT_WAIT_POSTCOMPACT, DEFAULT_MERGE_AFTER
    global DEFAULT_OPERATION_LOG, DEFAULT_LOG_RAW_PROMPT, COMPACT_COMMAND, ENTER_KEY

    _CONFIG = load_config_safe(config_path)
    readiness.refresh_config(config_path, strict=True)
    rolling_summary_writer.refresh_config(config_path)
    status.refresh_config(config_path, strict=True)
    _CONTROLLER_CONFIG = _CONFIG["controller"]
    READINESS_ORDER = {level: index for index, level in enumerate(_CONFIG["readiness"]["levels"])}
    DEFAULT_WAIT_TIMEOUT_SECONDS = float(_CONTROLLER_CONFIG["wait_timeout_seconds"])
    DEFAULT_POLL_INTERVAL_SECONDS = float(_CONTROLLER_CONFIG["poll_interval_seconds"])
    DEFAULT_TMUX_PATH = str(_CONTROLLER_CONFIG["tmux_path"])
    DEFAULT_MIN_READINESS = str(_CONTROLLER_CONFIG["min_readiness"])
    DEFAULT_PANE = str(_CONTROLLER_CONFIG.get("pane") or "") or None
    DEFAULT_NO_SEND = bool(_CONTROLLER_CONFIG["no_send"])
    DEFAULT_WAIT_POSTCOMPACT = bool(_CONTROLLER_CONFIG["wait_postcompact"])
    DEFAULT_MERGE_AFTER = bool(_CONTROLLER_CONFIG["merge_after"])
    DEFAULT_OPERATION_LOG = bool(_CONTROLLER_CONFIG["operation_log"])
    DEFAULT_LOG_RAW_PROMPT = bool(_CONTROLLER_CONFIG["log_raw_prompt"])
    COMPACT_COMMAND = str(_CONTROLLER_CONFIG["compact_command"])
    ENTER_KEY = str(_CONTROLLER_CONFIG["enter_key"])


@dataclass(frozen=True)
class ControllerConfig:
    pane: str | None
    confirm_send: bool
    no_send: bool
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
    parser.add_argument("--config", help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.")
    parser.add_argument("--pane", default=DEFAULT_PANE, help="explicit tmux target pane, for example session:window.pane")
    parser.add_argument("--confirm-send", action="store_true", help="compatibility no-op; tmux sends are enabled by default")
    parser.add_argument("--no-send", action="store_true", default=DEFAULT_NO_SEND, help="print the plan without sending tmux keys")
    parser.add_argument("--send", action="store_false", dest="no_send", help="send tmux keys even if config enables no_send")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt-file", type=Path, help="explicit file containing the prompt to send after compact")
    prompt_group.add_argument("--prompt-stdin", action="store_true", help="read the prompt to send from stdin")
    parser.add_argument("--min-readiness", choices=tuple(READINESS_ORDER), default=DEFAULT_MIN_READINESS)
    parser.add_argument("--wait-postcompact", action="store_true", default=DEFAULT_WAIT_POSTCOMPACT)
    parser.add_argument("--no-wait-postcompact", action="store_false", dest="wait_postcompact", help="disable postcompact waiting even if config enables it")
    parser.add_argument("--wait-timeout-seconds", type=positive_float, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=positive_float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--merge-after", action="store_true", default=DEFAULT_MERGE_AFTER)
    parser.add_argument("--no-merge-after", action="store_false", dest="merge_after", help="disable merge-after even if config enables it")
    parser.add_argument("--tmux-path", default=DEFAULT_TMUX_PATH)
    parser.add_argument("--operation-log", action="store_true", default=DEFAULT_OPERATION_LOG, help="append metadata-only controller operations to operation-log.jsonl")
    parser.add_argument("--no-operation-log", action="store_false", dest="operation_log", help="disable operation logging even if config enables it")
    parser.add_argument("--log-raw-prompt", action="store_true", default=DEFAULT_LOG_RAW_PROMPT, help="store bounded raw prompt text in operation-log.jsonl; sensitive")
    parser.add_argument("--no-log-raw-prompt", action="store_false", dest="log_raw_prompt", help="disable raw prompt logging even if config enables it")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ControllerConfig:
    return ControllerConfig(
        pane=args.pane,
        confirm_send=args.confirm_send,
        no_send=args.no_send,
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
    files = status.inspect_runtime()
    runtime_readiness = status.compact_readiness(files)
    estimated_chars = status.estimated_runtime_chars(files) + prompt.chars
    level = readiness.readiness_level(
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
            actions.append("write summary")
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
        f"pane: {config.pane or 'none'}",
        f"runtime_readiness: {plan.runtime_level}",
        f"readiness: {plan.readiness_level}",
        f"estimated_chars={plan.estimated_chars}",
        f"prompt_source={plan.prompt_source}",
        f"prompt_chars={plan.prompt_chars}",
        f"basis={readiness.READINESS_BASIS}",
        f"accuracy={readiness.READINESS_ACCURACY}",
        f"should_compact={'yes' if plan.should_compact else 'no'}",
        "actions: " + ", ".join(plan.actions),
    ]
    return "\n".join(lines) + "\n"


def operation_metadata(config: ControllerConfig, plan: ControllerPlan) -> dict[str, Any]:
    return {
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
    if not config.operation_log:
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
    if config.no_send:
        return None
    if (plan.should_compact or plan.prompt_chars > 0) and not config.pane:
        return "--pane is required before sending tmux keys"
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
            [tmux_path, "send-keys", "-t", pane, ENTER_KEY],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        return SendResult(1, type(exc).__name__)
    return SendResult(enter.returncode)


def history_snapshot() -> dict[str, Any]:
    info = status.inspect_jsonl(status.HISTORY)
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


def build_auto_summary(candidates: list[MemoryCandidate]) -> str:
    lines: list[str] = []
    for heading in _CONFIG["summary"]["rolling_summary_headings"]:
        lines.extend([str(heading), ""])
    if not candidates:
        lines.extend(["No compact history summaries found.", ""])
        return "\n".join(lines)
    for candidate in candidates:
        lines.extend([f"### {candidate.timestamp}", "", candidate.text, ""])
    return "\n".join(lines)


def write_summary_from_history() -> tuple[Path, Path | None]:
    summary_text = build_auto_summary(collect_recent_candidates(limit=merge_compact_history.MAX_DRAFT_SUMMARIES, service="auto-compact-controller"))
    return write_rolling_summary_with_backup(summary_text)


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

    compact_sent = False
    if config.no_send:
        log_controller_operation(config, "noop", "ok", plan, prompt, extra={"send_disabled": True})
        print("send_disabled=yes")
        return 0

    before_history = history_snapshot() if plan.should_compact and config.wait_postcompact else None
    if plan.should_compact:
        result = send_tmux_keys(config.tmux_path, config.pane or "", COMPACT_COMMAND)
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
        summary_path, backup_path = write_summary_from_history()
        extra = {"summary_path": str(summary_path)}
        if backup_path is not None:
            extra["backup_path"] = str(backup_path)
        log_controller_operation(config, "write-summary", "ok", plan, prompt, extra=extra)
        print(f"summary_written={summary_path}")
        if backup_path is not None:
            print(f"summary_backup={backup_path}")

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
    active_argv = sys.argv[1:] if argv is None else argv
    config_path = cli_config_path(active_argv)
    if config_path:
        os.environ[CONFIG_PATH_ENV] = config_path
    refresh_config(config_path)
    return run_controller(config_from_args(parse_args(active_argv)))


if __name__ == "__main__":
    raise SystemExit(main())
