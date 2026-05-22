from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_candidates import collect_recent_candidates
from merge_compact_history import DRAFT_NAME, MAX_DRAFT_SUMMARIES, build_draft
from sidecar_paths import runtime_dir, runtime_path

STATE_NAME = "daemon-state.json"


def state_payload(candidate_count: int, draft_path: Path) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "run-once",
        "candidate_count": candidate_count,
        "draft_path": str(draft_path),
        "draft_written": True,
    }


def run_once() -> int:
    candidates = collect_recent_candidates(limit=MAX_DRAFT_SUMMARIES)
    draft_path = runtime_path(DRAFT_NAME)
    state_path = runtime_path(STATE_NAME)

    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(build_draft(candidates), encoding="utf-8")
    state_path.write_text(json.dumps(state_payload(len(candidates), draft_path), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "Sidecar daemon run-once",
        f"runtime_dir: {runtime_dir()}",
        f"candidate_count: {len(candidates)}",
        f"draft_path: {draft_path}",
        "rolling-summary.md: not modified",
    ]
    print("\n".join(lines))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local sidecar daemon maintenance once.")
    parser.add_argument("--run-once", action="store_true", help="Run one local maintenance pass and exit.")
    args = parser.parse_args(argv)
    if not args.run_once:
        parser.error("--run-once is required; background daemon lifecycle is not implemented")
    return args


def main(argv: list[str] | None = None) -> int:
    parse_args(sys.argv[1:] if argv is None else argv)
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
