from __future__ import annotations

import argparse
from typing import Any

from memory_candidates import MemoryCandidate, collect_recent_candidates, extract_path_hints
from operation_log import append_operation
from sidecar_paths import runtime_path

MAX_DRAFT_SUMMARIES = 5
DRAFT_NAME = "rolling-summary.draft.md"


def build_draft(candidates: list[MemoryCandidate]) -> str:
    lines = [
        "# Rolling Summary Draft",
        "",
        "Review this draft manually, then copy only still-accurate information into rolling-summary.md.",
        "",
    ]
    if not candidates:
        lines.extend(["No compact history summaries found.", ""])
        return "\n".join(lines)

    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate.timestamp}",
                "",
                f"Source: {candidate.source_kind} / {candidate.source_file}",
                "",
            ]
        )
        hints = extract_path_hints(candidate.text)
        if hints:
            lines.extend(["Review hints from compact summary text only:", ""])
            lines.extend(f"- `{hint}`" for hint in hints)
            lines.append("")
        lines.extend([candidate.text, ""])
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate rolling-summary.draft.md from compact history.")
    parser.add_argument("--operation-log", action="store_true", help="append metadata-only draft generation operation to operation-log.jsonl")
    parser.add_argument("--log-raw-summary", action="store_true", help="store bounded generated draft text in operation-log.jsonl; sensitive")
    return parser.parse_args(argv)


def log_draft_operation(args: argparse.Namespace, status: str, metadata: dict[str, Any], draft_text: str = "") -> None:
    if not args.operation_log and not args.log_raw_summary:
        return
    raw = {"summary": draft_text} if args.log_raw_summary and draft_text else None
    append_operation(
        "merge-compact-history",
        "write-draft",
        status,
        metadata=metadata,
        raw=raw,
        content_policy={"raw_prompt_logged": False, "raw_summary_logged": bool(raw)},
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.log_raw_summary and not args.operation_log:
        print("--log-raw-summary requires --operation-log", flush=True)
        return 2

    draft_path = runtime_path(DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = collect_recent_candidates(limit=MAX_DRAFT_SUMMARIES)
    draft_text = build_draft(candidates)
    draft_path.write_text(draft_text, encoding="utf-8")
    log_draft_operation(
        args,
        "ok",
        {
            "candidate_count": len(candidates),
            "draft_path": str(draft_path),
            "draft_bytes": len(draft_text.encode("utf-8")),
            "source_files": sorted({candidate.source_file for candidate in candidates}),
        },
        draft_text,
    )
    print(f"Wrote {draft_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
