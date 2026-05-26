from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from readiness import compact_warning, readiness_level
from sidecar_paths import emit_json, noop_response, runtime_path
from summary_context import additional_context, read_rolling_summary

HOOK_EVENT_NAME = "UserPromptSubmit"
MAX_HOOK_STDIN_CHARS = 200_000
PROMPT_FIELDS = ("prompt", "userPrompt", "message", "input")
RUNTIME_PRESSURE_FILES = (
    "rolling-summary.draft.md",
    "compact-history.jsonl",
    "compact-history.jsonl.1",
    "daemon-state.json",
)


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
    if readiness_level(estimated_chars) != "high":
        return ""
    return compact_warning(estimated_chars)


def combined_context(summary: str | None, prompt: PromptEstimate) -> str:
    summary_context = additional_context(summary) if summary is not None else ""
    advisory = advisory_context(prompt, summary_context)
    if advisory and summary_context:
        return f"{advisory}\n\n{summary_context}"
    return advisory or summary_context


def main() -> int:
    summary = read_rolling_summary()
    context = combined_context(summary, current_prompt_estimate())
    if not context:
        emit_json(noop_response())
        return 0

    emit_json(build_response(context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
