from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sidecar_paths import runtime_path, write_error

MAX_DRAFT_SUMMARIES = 5
DRAFT_NAME = "rolling-summary.draft.md"
HISTORY_NAMES = ("compact-history.jsonl", "compact-history.jsonl.1")


def iter_history_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
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
                records.append(record)
    except Exception as exc:
        write_error(f"failed to read {path.name}", exc=exc)
    return records


def extract_summary(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


def collect_recent_summaries() -> list[tuple[str, str]]:
    records: list[dict[str, Any]] = []
    for name in HISTORY_NAMES:
        records.extend(iter_history_records(runtime_path(name)))

    records.sort(key=lambda record: str(record.get("timestamp", "")), reverse=True)

    summaries: list[tuple[str, str]] = []
    for record in records:
        summary = extract_summary(record)
        if summary is None:
            continue
        summaries.append((str(record.get("timestamp", "unknown timestamp")), summary))
        if len(summaries) >= MAX_DRAFT_SUMMARIES:
            break
    return summaries


def build_draft(summaries: list[tuple[str, str]]) -> str:
    lines = [
        "# Rolling Summary Draft",
        "",
        "Review this draft manually, then copy only still-accurate information into rolling-summary.md.",
        "",
    ]
    if not summaries:
        lines.extend(["No compact history summaries found.", ""])
        return "\n".join(lines)

    for timestamp, summary in summaries:
        lines.extend([f"## {timestamp}", "", summary, ""])
    return "\n".join(lines)


def main() -> int:
    draft_path = runtime_path(DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(build_draft(collect_recent_summaries()), encoding="utf-8")
    print(f"Wrote {draft_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
