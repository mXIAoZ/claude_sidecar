from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sidecar_paths import runtime_path, write_error

HISTORY_NAMES = ("compact-history.jsonl", "compact-history.jsonl.1")
DEFAULT_CANDIDATE_LIMIT = 5
DEFAULT_HINT_LIMIT = 5
UNKNOWN_TIMESTAMP = "unknown timestamp"
PATH_TOKEN_RE = re.compile(r"(?<![\w:/.-])(?:[\w.-]+/)+[\w.-]+|(?<![\w/.-])(?:CLAUDE|SPEC|README)\.md\b")
TRAILING_PATH_PUNCTUATION = "'\"`.,;:)>}]}"


@dataclass(frozen=True)
class MemoryCandidate:
    source_kind: str
    source_file: str
    timestamp: str
    text: str

def extract_path_hints(text: str, *, limit: int = DEFAULT_HINT_LIMIT) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for match in PATH_TOKEN_RE.finditer(text):
        hint = match.group(0).rstrip(TRAILING_PATH_PUNCTUATION)
        if "://" in hint or hint in seen:
            continue
        seen.add(hint)
        hints.append(hint)
        if len(hints) >= limit:
            break
    return hints


def iter_history_records(path: Path) -> list[tuple[str, dict[str, Any]]]:
    if not path.exists():
        return []

    records: list[tuple[str, dict[str, Any]]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                write_error(f"failed to parse {path.name} line", exc=exc)
                continue
            if isinstance(record, dict):
                records.append((path.name, record))
    except Exception as exc:
        write_error(f"failed to read {path.name}", exc=exc)
    return records


def candidate_from_record(source_file: str, record: dict[str, Any]) -> MemoryCandidate | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None

    return MemoryCandidate(
        source_kind="compact-history",
        source_file=source_file,
        timestamp=str(record.get("timestamp") or UNKNOWN_TIMESTAMP),
        text=summary.strip(),
    )


def collect_recent_candidates(*, limit: int = DEFAULT_CANDIDATE_LIMIT) -> list[MemoryCandidate]:
    records: list[tuple[str, dict[str, Any]]] = []
    for name in HISTORY_NAMES:
        records.extend(iter_history_records(runtime_path(name)))

    records.sort(key=lambda item: str(item[1].get("timestamp", "")), reverse=True)

    candidates: list[MemoryCandidate] = []
    for source_file, record in records:
        candidate = candidate_from_record(source_file, record)
        if candidate is None:
            continue
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates
