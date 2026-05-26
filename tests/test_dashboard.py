from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "dashboard.py"


class DashboardTests(unittest.TestCase):
    def run_dashboard(self, runtime_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def write_operation(self, runtime_dir: Path, record: dict) -> None:
        with (runtime_dir / "operation-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_missing_runtime_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            result = self.run_dashboard(runtime_dir)

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar Operations Dashboard", result.stdout)
        self.assertIn("status: empty", result.stdout)
        self.assertIn("No operation records found.", result.stdout)

    def test_dashboard_renders_runtime_files_and_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("## Compact 前必须保留\nkeep", encoding="utf-8")
            self.write_operation(
                runtime_dir,
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "controller",
                    "operation": "send-prompt",
                    "status": "ok",
                    "metadata": {"prompt_chars": 10, "readiness": "low"},
                    "content_policy": {"raw_prompt_logged": False, "raw_summary_logged": False},
                },
            )

            result = self.run_dashboard(runtime_dir, "--no-color")

        self.assertIn("rolling-summary.md: present", result.stdout)
        self.assertIn("operation-log.jsonl: present", result.stdout)
        self.assertIn("controller | send-prompt | ok", result.stdout)
        self.assertIn("prompt_chars=10", result.stdout)
        self.assertNotIn("\033[", result.stdout)

    def test_json_output_is_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_dashboard(runtime_dir, "--json")
            payload = json.loads(result.stdout)

        self.assertEqual(payload["status"], "empty")
        self.assertIn("readiness", payload)
        self.assertIn("files", payload)
        self.assertIn("operations", payload)

    def test_raw_content_hidden_by_default_and_shown_with_flag(self) -> None:
        secret = "RAW_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_operation(
                runtime_dir,
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "controller",
                    "operation": "send-prompt",
                    "status": "ok",
                    "metadata": {"prompt_chars": len(secret)},
                    "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                    "raw": {"prompt": secret},
                },
            )

            hidden = self.run_dashboard(runtime_dir)
            shown = self.run_dashboard(runtime_dir, "--show-content")

        self.assertNotIn(secret, hidden.stdout)
        self.assertIn("raw: hidden", hidden.stdout)
        self.assertIn(secret, shown.stdout)

    def test_json_hides_raw_content_unless_show_content_is_set(self) -> None:
        secret = "RAW_JSON_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_operation(
                runtime_dir,
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "controller",
                    "operation": "send-prompt",
                    "status": "ok",
                    "metadata": {"prompt_chars": len(secret)},
                    "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                    "raw": {"prompt": secret},
                },
            )

            hidden = self.run_dashboard(runtime_dir, "--json")
            shown = self.run_dashboard(runtime_dir, "--json", "--show-content")

        hidden_payload = json.loads(hidden.stdout)
        shown_payload = json.loads(shown.stdout)
        self.assertNotIn(secret, hidden.stdout)
        self.assertNotIn("raw", hidden_payload["operations"][0])
        self.assertEqual(shown_payload["operations"][0]["raw"]["prompt"], secret)

    def test_malformed_operation_log_reports_warning_without_writing_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "operation-log.jsonl").write_text("{\n", encoding="utf-8")

            result = self.run_dashboard(runtime_dir)

            self.assertFalse((runtime_dir / "errors.log").exists())

        self.assertIn("operation-log.jsonl: present", result.stdout)
        self.assertIn("malformed=1", result.stdout)
        self.assertIn("operation-log.jsonl has malformed records", result.stdout)


if __name__ == "__main__":
    unittest.main()
