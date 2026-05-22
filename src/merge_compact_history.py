from __future__ import annotations

from memory_candidates import MemoryCandidate, collect_recent_candidates, extract_path_hints
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


def main() -> int:
    draft_path = runtime_path(DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        build_draft(collect_recent_candidates(limit=MAX_DRAFT_SUMMARIES)),
        encoding="utf-8",
    )
    print(f"Wrote {draft_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
