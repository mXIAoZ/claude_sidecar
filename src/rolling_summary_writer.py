from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sidecar_paths import runtime_path

SUMMARY_NAME = "rolling-summary.md"
SUMMARY_BACKUP_PREFIX = "rolling-summary.backup"
REQUIRED_HEADING = "# Rolling Summary"
REQUIRED_MARKER = "## Compact 前必须保留"


class RollingSummaryError(Exception):
    pass


def summary_lines(summary_text: str) -> list[str]:
    return [line.strip() for line in summary_text.splitlines() if line.strip()]


def validate_rolling_summary_text(summary_text: str) -> str:
    if not isinstance(summary_text, str) or not summary_text.strip():
        raise RollingSummaryError("rolling summary text must be non-empty")
    lines = summary_lines(summary_text)
    if not lines or lines[0] != REQUIRED_HEADING:
        raise RollingSummaryError(f"rolling summary must start with {REQUIRED_HEADING}")
    if REQUIRED_MARKER not in lines:
        raise RollingSummaryError(f"rolling summary must include standalone {REQUIRED_MARKER}")
    return summary_text


def summary_backup_path(summary_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = summary_path.with_name(f"{SUMMARY_BACKUP_PREFIX}.{timestamp}.md")
    counter = 1
    while candidate.exists():
        candidate = summary_path.with_name(f"{SUMMARY_BACKUP_PREFIX}.{timestamp}.{counter}.md")
        counter += 1
    return candidate


def write_rolling_summary_with_backup(summary_text: str, summary_path: Path | None = None) -> tuple[Path, Path | None]:
    validated = validate_rolling_summary_text(summary_text)
    target = runtime_path(SUMMARY_NAME) if summary_path is None else summary_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(validated)
            temp_path = Path(handle.name)
        backup_path = None
        if target.exists():
            backup_path = summary_backup_path(target)
            shutil.copy2(target, backup_path)
        temp_path.replace(target)
        return target, backup_path
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RollingSummaryError(f"failed to write rolling summary: {exc}") from exc
