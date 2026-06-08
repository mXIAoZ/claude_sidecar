from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.runtime.memory_candidates import collect_recent_candidates, extract_path_hints


class MemoryCandidatesTests(unittest.TestCase):
    def write_record(self, path: Path, record: object) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def collect(self, runtime_dir: Path, *, limit: int = 5):
        original = os.environ.get("SIDECAR_COMPACT_DIR")
        os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        try:
            return collect_recent_candidates(limit=limit)
        finally:
            if original is None:
                os.environ.pop("SIDECAR_COMPACT_DIR", None)
            else:
                os.environ["SIDECAR_COMPACT_DIR"] = original

    def test_extracts_current_and_rotated_history_summaries_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_record(
                runtime_dir / "compact-history.jsonl.1",
                {"timestamp": "2026-05-20T10:00:00+00:00", "payload": {"summary": "older rotated"}},
            )
            self.write_record(
                runtime_dir / "compact-history.jsonl",
                {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "newer current"}},
            )

            candidates = self.collect(runtime_dir)

        self.assertEqual([candidate.text for candidate in candidates], ["newer current", "older rotated"])
        self.assertEqual(candidates[0].source_kind, "compact-history")
        self.assertEqual(candidates[0].source_file, "compact-history.jsonl")
        self.assertEqual(candidates[1].source_file, "compact-history.jsonl.1")

    def test_ignores_records_without_non_empty_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            self.write_record(history_path, {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "  "}})
            self.write_record(history_path, {"timestamp": "2026-05-21T11:00:00+00:00", "payload": {"other": "value"}})
            self.write_record(history_path, {"timestamp": "2026-05-21T12:00:00+00:00", "payload": "not object"})
            self.write_record(history_path, ["not object"])

            candidates = self.collect(runtime_dir)

        self.assertEqual(candidates, [])

    def test_malformed_jsonl_logs_error_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            history_path.write_text("{\n", encoding="utf-8")
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "valid after bad line"}},
            )

            candidates = self.collect(runtime_dir)
            errors = (runtime_dir / "errors.log").read_text(encoding="utf-8")

        self.assertEqual([candidate.text for candidate in candidates], ["valid after bad line"])
        self.assertIn("failed to parse compact-history.jsonl line", errors)

    def test_limits_candidate_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            for index in range(3):
                self.write_record(
                    history_path,
                    {
                        "timestamp": f"2026-05-21T1{index}:00:00+00:00",
                        "payload": {"summary": f"summary {index}"},
                    },
                )

            candidates = self.collect(runtime_dir, limit=2)

        self.assertEqual([candidate.text for candidate in candidates], ["summary 2", "summary 1"])

    def test_dedupes_duplicate_summaries_newest_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_record(
                runtime_dir / "compact-history.jsonl.1",
                {"timestamp": "2026-05-20T10:00:00+00:00", "payload": {"summary": "duplicate summary"}},
            )
            self.write_record(
                runtime_dir / "compact-history.jsonl",
                {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "duplicate summary"}},
            )

            candidates = self.collect(runtime_dir)

        self.assertEqual([candidate.text for candidate in candidates], ["duplicate summary"])
        self.assertEqual(candidates[0].timestamp, "2026-05-21T10:00:00+00:00")
        self.assertEqual(candidates[0].source_file, "compact-history.jsonl")

    def test_dedupes_whitespace_and_case_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T12:00:00+00:00", "payload": {"summary": "  SAME\nsummary  "}},
            )
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T11:00:00+00:00", "payload": {"summary": "same   SUMMARY"}},
            )

            candidates = self.collect(runtime_dir)

        self.assertEqual([candidate.text for candidate in candidates], ["SAME\nsummary"])
        self.assertEqual(candidates[0].timestamp, "2026-05-21T12:00:00+00:00")

    def test_preserves_distinct_summaries_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "first unique"}},
            )
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T12:00:00+00:00", "payload": {"summary": "second unique"}},
            )

            candidates = self.collect(runtime_dir)

        self.assertEqual([candidate.text for candidate in candidates], ["second unique", "first unique"])

    def test_limit_applies_after_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T13:00:00+00:00", "payload": {"summary": "duplicate"}},
            )
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T12:00:00+00:00", "payload": {"summary": "duplicate"}},
            )
            self.write_record(
                history_path,
                {"timestamp": "2026-05-21T11:00:00+00:00", "payload": {"summary": "older unique"}},
            )

            candidates = self.collect(runtime_dir, limit=2)

        self.assertEqual([candidate.text for candidate in candidates], ["duplicate", "older unique"])

    def test_missing_timestamp_uses_unknown_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_record(runtime_dir / "compact-history.jsonl", {"payload": {"summary": "no timestamp"}})

            candidates = self.collect(runtime_dir)

        self.assertEqual(candidates[0].timestamp, "unknown timestamp")

    def test_extract_path_hints_from_summary_text(self) -> None:
        text = "Updated src/memory_candidates.py, tests/test_memory_candidates.py, and .memory/rolling-summary.md."

        hints = extract_path_hints(text)

        self.assertEqual(
            hints,
            ["src/memory_candidates.py", "tests/test_memory_candidates.py", ".memory/rolling-summary.md"],
        )

    def test_extract_path_hints_includes_known_docs(self) -> None:
        hints = extract_path_hints("Reviewed CLAUDE.md and SPEC.md.")

        self.assertEqual(hints, ["CLAUDE.md", "SPEC.md"])

    def test_extract_path_hints_ignores_urls_and_dedupes(self) -> None:
        text = "See https://example.com/src/ignored.py and src/keep.py; src/keep.py changed again."

        hints = extract_path_hints(text)

        self.assertEqual(hints, ["src/keep.py"])

    def test_extract_path_hints_respects_limit(self) -> None:
        text = " ".join(f"src/file_{index}.py" for index in range(4))

        hints = extract_path_hints(text, limit=2)

        self.assertEqual(hints, ["src/file_0.py", "src/file_1.py"])

    def test_extract_path_hints_returns_empty_for_plain_text(self) -> None:
        self.assertEqual(extract_path_hints("no file mentions here"), [])


if __name__ == "__main__":
    unittest.main()
