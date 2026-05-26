from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import operation_log
from operation_log import (
    MAX_RAW_CONTENT_CHARS,
    OPERATION_LOG,
    ROTATED_OPERATION_LOG,
    append_operation,
    inspect_operation_log,
    read_operation_records,
)


class OperationLogTests(unittest.TestCase):
    def with_runtime(self, runtime_dir: Path) -> None:
        os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)

    def test_append_metadata_record_without_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)

            append_operation(
                "controller",
                "dry-run",
                "ok",
                metadata={"prompt_chars": 12, "readiness": "low"},
            )
            records = read_operation_records()
            text = (runtime_dir / OPERATION_LOG).read_text(encoding="utf-8")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["service"], "controller")
        self.assertEqual(records[0]["metadata"]["prompt_chars"], 12)
        self.assertNotIn("raw", records[0])
        self.assertNotIn("secret prompt", text)

    def test_raw_content_requires_raw_argument_and_is_bounded(self) -> None:
        secret = "S" * (MAX_RAW_CONTENT_CHARS + 10)
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)

            append_operation("controller", "send-prompt", "ok", raw={"prompt": secret})
            record = read_operation_records()[0]

        self.assertEqual(len(record["raw"]["prompt"]), MAX_RAW_CONTENT_CHARS)
        self.assertTrue(record["content_policy"]["raw_prompt_logged"])

    def test_inspect_operation_log_reports_latest_and_raw_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)
            path = runtime_dir / OPERATION_LOG
            records = [
                {
                    "timestamp": "2026-05-21T10:00:00+00:00",
                    "service": "a",
                    "operation": "one",
                    "status": "ok",
                    "content_policy": {"raw_prompt_logged": False, "raw_summary_logged": False},
                },
                {
                    "timestamp": "2026-05-21T11:00:00+00:00",
                    "service": "b",
                    "operation": "two",
                    "status": "ok",
                    "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                },
            ]
            path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

            info = inspect_operation_log()

        self.assertTrue(info["exists"])
        self.assertEqual(info["records"], 2)
        self.assertEqual(info["latest"], "2026-05-21T11:00:00+00:00")
        self.assertTrue(info["raw_prompt_logged"])
        self.assertFalse(info["raw_summary_logged"])

    def test_malformed_log_reports_malformed_without_writing_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)
            (runtime_dir / OPERATION_LOG).write_text("{\n", encoding="utf-8")

            info = inspect_operation_log()

            self.assertFalse((runtime_dir / "errors.log").exists())

        self.assertEqual(info["malformed"], 1)
        self.assertEqual(info["records"], 0)

    def test_read_records_includes_rotated_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)
            (runtime_dir / OPERATION_LOG).write_text(
                json.dumps({"timestamp": "2026-05-21T11:00:00+00:00", "service": "new"}) + "\n",
                encoding="utf-8",
            )
            (runtime_dir / ROTATED_OPERATION_LOG).write_text(
                json.dumps({"timestamp": "2026-05-21T10:00:00+00:00", "service": "old"}) + "\n",
                encoding="utf-8",
            )

            records = read_operation_records(include_rotated=True)

        self.assertEqual([record["service"] for record in records], ["new", "old"])

    def test_append_rotates_before_new_record_exceeds_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)
            original_limit = operation_log.MAX_OPERATION_LOG_BYTES
            operation_log.MAX_OPERATION_LOG_BYTES = 250
            try:
                existing = json.dumps({"timestamp": "2026-05-21T10:00:00+00:00", "service": "existing"}) + "\n"
                (runtime_dir / OPERATION_LOG).write_text(existing, encoding="utf-8")
                append_operation("controller", "after-rotate", "ok", metadata={"payload": "x" * 200})
            finally:
                operation_log.MAX_OPERATION_LOG_BYTES = original_limit

            current = (runtime_dir / OPERATION_LOG).read_text(encoding="utf-8")
            rotated = (runtime_dir / ROTATED_OPERATION_LOG).read_text(encoding="utf-8")

        self.assertIn("after-rotate", current)
        self.assertNotIn("existing", current)
        self.assertEqual(rotated, existing)

    def test_append_rotates_large_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.with_runtime(runtime_dir)
            original_limit = operation_log.MAX_OPERATION_LOG_BYTES
            operation_log.MAX_OPERATION_LOG_BYTES = 10
            try:
                (runtime_dir / OPERATION_LOG).write_text("x" * 11, encoding="utf-8")
                append_operation("controller", "after-rotate", "ok")
            finally:
                operation_log.MAX_OPERATION_LOG_BYTES = original_limit

            current = (runtime_dir / OPERATION_LOG).read_text(encoding="utf-8")
            rotated = (runtime_dir / ROTATED_OPERATION_LOG).read_text(encoding="utf-8")

        self.assertIn("after-rotate", current)
        self.assertEqual(rotated, "x" * 11)


if __name__ == "__main__":
    unittest.main()
