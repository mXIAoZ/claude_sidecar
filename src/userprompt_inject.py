from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

import readiness
from sidecar_config import SidecarConfigError, load_config_for_import, load_config_safe
from sidecar_paths import emit_json, noop_response, runtime_path
import summary_context

_CONFIG = load_config_for_import()
HOOK_EVENT_NAME = str(_CONFIG["hooks"]["userprompt_event_name"])
MAX_HOOK_STDIN_CHARS = int(_CONFIG["hooks"]["userprompt_stdin_max_chars"])
PROMPT_FIELDS = tuple(str(field) for field in _CONFIG["hooks"]["prompt_fields"])
RUNTIME_PRESSURE_FILES = tuple(str(name) for name in _CONFIG["readiness"]["runtime_pressure_files"])


def refresh_config() -> None:
    global _CONFIG, HOOK_EVENT_NAME, MAX_HOOK_STDIN_CHARS, PROMPT_FIELDS, RUNTIME_PRESSURE_FILES
    _CONFIG = load_config_safe()
    readiness.refresh_config(strict=True)
    summary_context.refresh_config(strict=True)
    HOOK_EVENT_NAME = str(_CONFIG["hooks"]["userprompt_event_name"])
    MAX_HOOK_STDIN_CHARS = int(_CONFIG["hooks"]["userprompt_stdin_max_chars"])
    PROMPT_FIELDS = tuple(str(field) for field in _CONFIG["hooks"]["prompt_fields"])
    RUNTIME_PRESSURE_FILES = tuple(str(name) for name in _CONFIG["readiness"]["runtime_pressure_files"])


@dataclass(frozen=True)
class PromptEstimate:
    text: str
    estimated_chars: int


def build_response(context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "additionalContext": context,
        }
    }


def read_stdin_capped() -> tuple[str, bool]:
    try:
        if sys.stdin.isatty():
            return "", False
        raw = sys.stdin.read(MAX_HOOK_STDIN_CHARS + 1)
    except OSError:
        return "", False
    return raw[:MAX_HOOK_STDIN_CHARS], len(raw) > MAX_HOOK_STDIN_CHARS


def prompt_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for field in PROMPT_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            return value
    return ""


def current_prompt_estimate() -> PromptEstimate:
    raw, capped = read_stdin_capped()
    stripped = raw.strip()
    if not stripped:
        return PromptEstimate("", 0)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return PromptEstimate("", len(raw) if capped else 0)
    prompt = prompt_from_payload(payload)
    return PromptEstimate(prompt, len(prompt))


def runtime_pressure_chars() -> int:
    total = 0
    for name in RUNTIME_PRESSURE_FILES:
        total += file_size(name)
    return total


def file_size(name: str) -> int:
    try:
        return runtime_path(name).stat().st_size
    except OSError:
        return 0


def advisory_context(prompt: PromptEstimate, summary_context: str) -> str:
    estimated_chars = runtime_pressure_chars() + prompt.estimated_chars + len(summary_context)
    if readiness.readiness_level(estimated_chars) != "high":
        return ""
    return readiness.compact_warning(estimated_chars)


def combined_context(summary: str | None, prompt: PromptEstimate) -> str:
    summary_context_text = summary_context.additional_context(summary) if summary is not None else ""
    advisory = advisory_context(prompt, summary_context_text)
    if advisory and summary_context_text:
        return f"{advisory}\n\n{summary_context_text}"
    return advisory or summary_context_text


def main() -> int:
    try:
        refresh_config()
    except SidecarConfigError:
        emit_json(noop_response())
        return 0
    summary = summary_context.read_rolling_summary()
    context = combined_context(summary, current_prompt_estimate())
    if not context:
        emit_json(noop_response())
        return 0

    emit_json(build_response(context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
