from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "postcompact_record.py"
KEEP_DIR_ENV = "SIDECAR_TEST_KEEP_DIR"


@contextmanager
def runtime_dir_for_test(test_name: str) -> Iterator[Path]:
    keep_root = os.environ.get(KEEP_DIR_ENV)
    if keep_root:
        runtime_dir = Path(keep_root).expanduser() / test_name
        shutil.rmtree(runtime_dir, ignore_errors=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        yield runtime_dir
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


class PostcompactRecordTests(unittest.TestCase):
    def run_script(self, runtime_dir: Path, stdin: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=stdin,
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_valid_payload_appends_jsonl_record(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            result = self.run_script(runtime_dir, '{"session_id":"test","summary":"compacted"}')
            history_path = runtime_dir / "compact-history.jsonl"
            records = history_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 1)
        record = json.loads(records[0])
        expected_payload = {"session_id": "test", "summary": "compacted"}
        expected_payload_json = json.dumps(expected_payload, ensure_ascii=False, sort_keys=True)
        self.assertEqual(record["payload"], expected_payload)
        self.assertEqual(record["payload_bytes"], len(expected_payload_json.encode("utf-8")))
        self.assertIn("timestamp", record)

    def test_empty_stdin_appends_empty_payload_record(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            result = self.run_script(runtime_dir, "")
            history_path = runtime_dir / "compact-history.jsonl"
            records = history_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(records), 1)
        record = json.loads(records[0])
        self.assertEqual(record["payload"], {})
        self.assertEqual(record["payload_bytes"], len("{}".encode("utf-8")))
        self.assertIn("timestamp", record)

    def test_oversized_history_file_is_rotated_before_append(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            history_path = runtime_dir / "compact-history.jsonl"
            rotated_path = runtime_dir / "compact-history.jsonl.1"
            history_path.write_text("x" * (5_000_001), encoding="utf-8")

            result = self.run_script(runtime_dir, '{"session_id":"next"}')
            records = history_path.read_text(encoding="utf-8").splitlines()
            rotated_exists = rotated_path.exists()
            rotated_size = rotated_path.stat().st_size

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(rotated_exists)
        self.assertEqual(rotated_size, 5_000_001)
        self.assertEqual(len(records), 1)
        record = json.loads(records[0])
        self.assertEqual(record["payload"]["session_id"], "next")

    def test_oversized_payload_logs_error_without_history(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            result = self.run_script(runtime_dir, " " * 200_001)
            error_log = runtime_dir / "errors.log"
            history_path = runtime_dir / "compact-history.jsonl"
            errors = error_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(history_path.exists())
        self.assertEqual(len(errors), 1)
        self.assertIn("PostCompact hook payload exceeded size limit", errors[0])

    def test_malformed_payload_logs_error_without_history(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            result = self.run_script(runtime_dir, "{")
            error_log = runtime_dir / "errors.log"
            history_path = runtime_dir / "compact-history.jsonl"
            errors = error_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(history_path.exists())
        self.assertEqual(len(errors), 1)
        self.assertIn("failed to parse PostCompact hook payload", errors[0])

    def test_non_object_payload_logs_error_without_history(self) -> None:
        with runtime_dir_for_test(self._testMethodName) as runtime_dir:
            result = self.run_script(runtime_dir, "[]")
            error_log = runtime_dir / "errors.log"
            history_path = runtime_dir / "compact-history.jsonl"
            errors = error_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(history_path.exists())
        self.assertEqual(len(errors), 1)
        self.assertIn("PostCompact hook payload was not a JSON object", errors[0])


if __name__ == "__main__":
    unittest.main()
