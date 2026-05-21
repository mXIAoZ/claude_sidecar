from __future__ import annotations

import os

from sidecar_paths import runtime_path, write_error

INJECT_ALWAYS_ENV = "SIDECAR_INJECT_ALWAYS"
INJECTION_MARKER = "## Compact 前必须保留"
MAX_SUMMARY_CHARS = 12_000
HEAD_CHARS = 6_000
TAIL_CHARS = 5_000
TRUNCATION_NOTICE = (
    "\n\n[Sidecar compact note: rolling-summary.md exceeded the prompt injection "
    "limit, so the middle was truncated. Please trim rolling-summary.md.]\n\n"
)


def should_inject_summary(summary: str) -> bool:
    return os.environ.get(INJECT_ALWAYS_ENV) == "1" or INJECTION_MARKER in summary


def compact_summary(summary: str) -> str:
    if len(summary) <= MAX_SUMMARY_CHARS:
        return summary
    return summary[:HEAD_CHARS].rstrip() + TRUNCATION_NOTICE + summary[-TAIL_CHARS:].lstrip()


def read_rolling_summary() -> str | None:
    try:
        summary = runtime_path("rolling-summary.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        write_error("failed to read rolling-summary.md", exc=exc)
        return None

    if not summary.strip() or not should_inject_summary(summary):
        return None
    return compact_summary(summary)


def additional_context(summary: str) -> str:
    return "Sidecar rolling summary for continuity preservation:\n" + summary
