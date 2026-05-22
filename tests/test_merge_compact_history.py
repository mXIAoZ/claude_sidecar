from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "merge_compact_history.py"


class MergeCompactHistoryTests(unittest.TestCase):
    def run_script(self, runtime_dir: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def write_history_record(self, path: Path, timestamp: str, summary: str) -> None:
        record = {
            "timestamp": timestamp,
            "payload": {"summary": summary},
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_generates_draft_from_recent_history_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(
                runtime_dir / "compact-history.jsonl.1",
                "2026-05-20T10:00:00+00:00",
                "older rotated summary",
            )
            self.write_history_record(
                runtime_dir / "compact-history.jsonl",
                "2026-05-21T10:00:00+00:00",
                "newer current summary",
            )

            result = self.run_script(runtime_dir)
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(result.stderr, "")
        self.assertIn("rolling-summary.draft.md", result.stdout)
        self.assertIn("# Rolling Summary Draft", draft)
        self.assertIn("Source: compact-history / compact-history.jsonl", draft)
        self.assertIn("Source: compact-history / compact-history.jsonl.1", draft)
        self.assertLess(draft.index("newer current summary"), draft.index("older rotated summary"))

    def test_does_not_overwrite_existing_rolling_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("keep this", encoding="utf-8")
            self.write_history_record(
                runtime_dir / "compact-history.jsonl",
                "2026-05-21T10:00:00+00:00",
                "draft only summary",
            )

            self.run_script(runtime_dir)
            rolling_summary = (runtime_dir / "rolling-summary.md").read_text(encoding="utf-8")
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(rolling_summary, "keep this")
        self.assertIn("draft only summary", draft)

    def test_draft_includes_path_review_hints_from_summary_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(
                runtime_dir / "compact-history.jsonl",
                "2026-05-21T10:00:00+00:00",
                "Touched src/memory_candidates.py and tests/test_memory_candidates.py",
            )

            self.run_script(runtime_dir)
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertIn("Review hints from compact summary text only:", draft)
        self.assertIn("- `src/memory_candidates.py`", draft)
        self.assertIn("- `tests/test_memory_candidates.py`", draft)

    def test_missing_history_writes_empty_draft_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_script(runtime_dir)
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(result.stderr, "")
        self.assertIn("No compact history summaries found.", draft)


if __name__ == "__main__":
    unittest.main()
