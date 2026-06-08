from __future__ import annotations

import os

from compact_sidecar.config import load_config_for_import, load_config_safe
from compact_sidecar.paths import runtime_path, write_error

_CONFIG = load_config_for_import()
_SUMMARY_CONFIG = _CONFIG["summary"]
INJECT_ALWAYS_ENV = "SIDECAR_INJECT_ALWAYS"
INJECTION_MARKER = str(_SUMMARY_CONFIG["injection_marker"])
INJECT_ALWAYS_DEFAULT = bool(_SUMMARY_CONFIG["inject_always"])
MAX_SUMMARY_CHARS = int(_SUMMARY_CONFIG["max_summary_chars"])
HEAD_CHARS = int(_SUMMARY_CONFIG["head_chars"])
TAIL_CHARS = int(_SUMMARY_CONFIG["tail_chars"])
TRUNCATION_NOTICE = str(_SUMMARY_CONFIG["truncation_notice"])
ADDITIONAL_CONTEXT_PREFIX = str(_SUMMARY_CONFIG["additional_context_prefix"])
SUMMARY_NAME = str(_CONFIG["paths"]["runtime_files"]["rolling_summary"])


def refresh_config(config_path: str | None = None, *, strict: bool = False) -> None:
    global _CONFIG, _SUMMARY_CONFIG, INJECTION_MARKER, INJECT_ALWAYS_DEFAULT, MAX_SUMMARY_CHARS
    global HEAD_CHARS, TAIL_CHARS, TRUNCATION_NOTICE, ADDITIONAL_CONTEXT_PREFIX, SUMMARY_NAME
    _CONFIG = load_config_safe(config_path) if strict or config_path else load_config_for_import()
    _SUMMARY_CONFIG = _CONFIG["summary"]
    INJECTION_MARKER = str(_SUMMARY_CONFIG["injection_marker"])
    INJECT_ALWAYS_DEFAULT = bool(_SUMMARY_CONFIG["inject_always"])
    MAX_SUMMARY_CHARS = int(_SUMMARY_CONFIG["max_summary_chars"])
    HEAD_CHARS = int(_SUMMARY_CONFIG["head_chars"])
    TAIL_CHARS = int(_SUMMARY_CONFIG["tail_chars"])
    TRUNCATION_NOTICE = str(_SUMMARY_CONFIG["truncation_notice"])
    ADDITIONAL_CONTEXT_PREFIX = str(_SUMMARY_CONFIG["additional_context_prefix"])
    SUMMARY_NAME = str(_CONFIG["paths"]["runtime_files"]["rolling_summary"])


def should_inject_summary(summary: str) -> bool:
    return os.environ.get(INJECT_ALWAYS_ENV) == "1" or INJECT_ALWAYS_DEFAULT or INJECTION_MARKER in summary


def compact_summary(summary: str) -> str:
    if len(summary) <= MAX_SUMMARY_CHARS:
        return summary
    return summary[:HEAD_CHARS].rstrip() + TRUNCATION_NOTICE + summary[-TAIL_CHARS:].lstrip()


def read_rolling_summary() -> str | None:
    try:
        summary = runtime_path(SUMMARY_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        write_error(f"failed to read {SUMMARY_NAME}", exc=exc)
        return None

    if not summary.strip() or not should_inject_summary(summary):
        return None
    return compact_summary(summary)


def additional_context(summary: str) -> str:
    return ADDITIONAL_CONTEXT_PREFIX + summary
