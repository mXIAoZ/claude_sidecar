from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE = "compact_sidecar.runtime.merge_compact_history"


class MergeCompactHistoryTests(unittest.TestCase):
    def run_script(self, runtime_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, "-m", MODULE, *args],
            check=check,
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

    def test_draft_dedupes_repeated_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(
                runtime_dir / "compact-history.jsonl",
                "2026-05-21T11:00:00+00:00",
                "same summary",
            )
            self.write_history_record(
                runtime_dir / "compact-history.jsonl.1",
                "2026-05-20T11:00:00+00:00",
                "same   summary",
            )

            self.run_script(runtime_dir)
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(draft.count("same summary"), 1)
        self.assertNotIn("same   summary", draft)


    def read_operation_records(self, runtime_dir: Path) -> list[dict]:
        path = runtime_dir / "operation-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_operation_log_records_draft_metadata_without_raw_summary_by_default(self) -> None:
        summary = "MERGE_SECRET_SUMMARY"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", "2026-05-21T10:00:00+00:00", summary)

            result = self.run_script(runtime_dir, "--operation-log")
            records = self.read_operation_records(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["service"], "merge-compact-history")
        self.assertEqual(records[0]["metadata"]["candidate_count"], 1)
        self.assertNotIn("raw", records[0])
        self.assertNotIn(summary, json.dumps(records[0], ensure_ascii=False))

    def test_log_raw_summary_requires_operation_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_script(runtime_dir, "--log-raw-summary", check=False)

        self.assertEqual(result.returncode, 2)
        self.assertIn("--log-raw-summary requires --operation-log", result.stdout)

    def test_log_raw_summary_stores_generated_draft_when_enabled(self) -> None:
        summary = "RAW_MERGE_SUMMARY"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", "2026-05-21T10:00:00+00:00", summary)

            result = self.run_script(runtime_dir, "--operation-log", "--log-raw-summary")
            records = self.read_operation_records(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn(summary, records[0]["raw"]["summary"])
        self.assertTrue(records[0]["content_policy"]["raw_summary_logged"])

    def test_missing_history_writes_empty_draft_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_script(runtime_dir)
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(result.stderr, "")
        self.assertIn("No compact history summaries found.", draft)


if __name__ == "__main__":
    unittest.main()
