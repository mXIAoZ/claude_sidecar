from __future__ import annotations

from sidecar_paths import emit_json, noop_response
from summary_context import additional_context, read_rolling_summary

HOOK_EVENT_NAME = "UserPromptSubmit"


def build_response(summary: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "additionalContext": additional_context(summary),
        }
    }


def main() -> int:
    summary = read_rolling_summary()
    if summary is None:
        emit_json(noop_response())
        return 0

    emit_json(build_response(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
