from __future__ import annotations

from sidecar_config import load_config_for_import, load_config_safe

_CONFIG = load_config_for_import()
_READINESS_CONFIG = _CONFIG["readiness"]
READINESS_MEDIUM_CHARS = int(_READINESS_CONFIG["medium_chars"])
READINESS_HIGH_CHARS = int(_READINESS_CONFIG["high_chars"])
READINESS_BASIS = str(_READINESS_CONFIG["basis"])
READINESS_ACCURACY = str(_READINESS_CONFIG["accuracy"])
COMPACT_ADVISORY_TITLE = str(_READINESS_CONFIG["advisory_title"])
COMPACT_ADVISORY_LINES = tuple(str(line) for line in _READINESS_CONFIG["advisory_lines"])


def refresh_config(config_path: str | None = None, *, strict: bool = False) -> None:
    global _CONFIG, _READINESS_CONFIG, READINESS_MEDIUM_CHARS, READINESS_HIGH_CHARS
    global READINESS_BASIS, READINESS_ACCURACY, COMPACT_ADVISORY_TITLE, COMPACT_ADVISORY_LINES
    _CONFIG = load_config_safe(config_path) if strict or config_path else load_config_for_import()
    _READINESS_CONFIG = _CONFIG["readiness"]
    READINESS_MEDIUM_CHARS = int(_READINESS_CONFIG["medium_chars"])
    READINESS_HIGH_CHARS = int(_READINESS_CONFIG["high_chars"])
    READINESS_BASIS = str(_READINESS_CONFIG["basis"])
    READINESS_ACCURACY = str(_READINESS_CONFIG["accuracy"])
    COMPACT_ADVISORY_TITLE = str(_READINESS_CONFIG["advisory_title"])
    COMPACT_ADVISORY_LINES = tuple(str(line) for line in _READINESS_CONFIG["advisory_lines"])


def readiness_level(estimated_chars: int, *, attention: bool = False) -> str:
    if attention:
        return "attention"
    if estimated_chars >= READINESS_HIGH_CHARS:
        return "high"
    if estimated_chars >= READINESS_MEDIUM_CHARS:
        return "medium"
    return "low"


def compact_warning(estimated_chars: int) -> str:
    return "\n".join(
        [
            COMPACT_ADVISORY_TITLE,
            *COMPACT_ADVISORY_LINES,
            f"Estimated local pressure: {estimated_chars} chars; accuracy={READINESS_ACCURACY}; basis={READINESS_BASIS}.",
        ]
    )
