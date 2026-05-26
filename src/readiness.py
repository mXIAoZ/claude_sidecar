from __future__ import annotations

READINESS_MEDIUM_CHARS = 80_000
READINESS_HIGH_CHARS = 160_000
READINESS_BASIS = "local-runtime-file-sizes"
READINESS_ACCURACY = "approximate"
COMPACT_ADVISORY_TITLE = "Sidecar compact-readiness advisory:"


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
            "This input may push the session near the approximate compact threshold.",
            "Consider running /compact before continuing, then resend the prompt.",
            f"Estimated local pressure: {estimated_chars} chars; accuracy=approximate; basis={READINESS_BASIS}.",
        ]
    )
