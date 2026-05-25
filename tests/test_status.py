from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "status.py"


class StatusCommandTests(unittest.TestCase):
    def run_status(self, runtime_dir: Path, *, inject_always: bool = False) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        if inject_always:
            env["SIDECAR_INJECT_ALWAYS"] = "1"
        else:
            env.pop("SIDECAR_INJECT_ALWAYS", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def write_jsonl(self, path: Path, *records: object) -> None:
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_missing_runtime_dir_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            result = self.run_status(runtime_dir)

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("status: empty", result.stdout)
        self.assertIn("rolling-summary.md: absent", result.stdout)
        self.assertIn("compact-history.jsonl: absent", result.stdout)

    def test_marker_summary_reports_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text(
                "## Compact 前必须保留\nSIDE_CAR_TEST_MARKER\n",
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("rolling-summary.md: present", result.stdout)
        self.assertIn("marker=yes", result.stdout)
        self.assertIn("injectable=yes", result.stdout)
        self.assertIn("status: ready", result.stdout)

    def test_summary_without_marker_reports_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("summary without marker", encoding="utf-8")
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("marker=no", result.stdout)
        self.assertIn("injectable=no", result.stdout)
        self.assertIn("status: inactive", result.stdout)

    def test_inject_always_reports_injectable_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("summary without marker", encoding="utf-8")
            result = self.run_status(runtime_dir, inject_always=True)

        self.assertEqual(result.stderr, "")
        self.assertIn("marker=no", result.stdout)
        self.assertIn("injectable=yes", result.stdout)
        self.assertIn("status: ready", result.stdout)

    def test_history_draft_and_errors_report_counts_and_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_jsonl(
                runtime_dir / "compact-history.jsonl",
                {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "one"}},
                {"timestamp": "2026-05-21T11:00:00+00:00", "payload": {"summary": "two"}},
            )
            self.write_jsonl(
                runtime_dir / "compact-history.jsonl.1",
                {"timestamp": "2026-05-20T10:00:00+00:00", "payload": {"summary": "older"}},
            )
            (runtime_dir / "rolling-summary.draft.md").write_text("draft", encoding="utf-8")
            self.write_jsonl(runtime_dir / "errors.log", {"timestamp": "2026-05-21T12:00:00+00:00", "message": "err"})

            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("rolling-summary.draft.md: present", result.stdout)
        self.assertIn("compact-history.jsonl: present", result.stdout)
        self.assertIn("records=2", result.stdout)
        self.assertIn("latest=2026-05-21T11:00:00+00:00", result.stdout)
        self.assertIn("compact-history.jsonl.1: present", result.stdout)
        self.assertIn("records=1", result.stdout)
        self.assertIn("errors.log: present", result.stdout)
        self.assertIn("latest=2026-05-21T12:00:00+00:00", result.stdout)
        self.assertIn("status: attention", result.stdout)

    def test_malformed_jsonl_reports_warning_without_writing_errors_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "compact-history.jsonl").write_text("{\n", encoding="utf-8")
            result = self.run_status(runtime_dir)
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("malformed=1", result.stdout)
        self.assertIn("status: attention", result.stdout)

    def test_invalid_utf8_reports_read_error_without_writing_errors_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "compact-history.jsonl").write_bytes(b"\xff")
            result = self.run_status(runtime_dir)
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("compact-history.jsonl: present", result.stdout)
        self.assertIn("read_error=yes", result.stdout)
        self.assertIn("status: attention", result.stdout)

    def test_valid_daemon_state_reports_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "run-once",
                        "candidate_count": 2,
                        "draft_path": str(runtime_dir / "rolling-summary.draft.md"),
                        "draft_written": True,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("daemon-state.json: present", result.stdout)
        self.assertIn("mode=run-once", result.stdout)
        self.assertIn("last_run=2026-05-21T12:00:00+00:00", result.stdout)
        self.assertIn("candidate_count=2", result.stdout)
        self.assertIn("status: inactive", result.stdout)

    def test_valid_daemon_state_reports_loop_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "loop",
                        "candidate_count": 2,
                        "interval_seconds": 60,
                        "run_count": 3,
                        "shutdown_reason": "max-runs",
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("mode=loop", result.stdout)
        self.assertIn("interval_seconds=60", result.stdout)
        self.assertIn("run_count=3", result.stdout)
        self.assertIn("shutdown_reason=max-runs", result.stdout)

    def test_valid_daemon_state_reports_lifecycle_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            plist_path = runtime_dir / "sidecar.plist"
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "remove-agent",
                        "plist_path": str(plist_path),
                        "label": "com.claude-code-compact-sidecar.daemon",
                        "plist_removed": True,
                        "launchctl_invoked": False,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("mode=remove-agent", result.stdout)
        self.assertIn(f"plist_path={plist_path}", result.stdout)
        self.assertIn("label=com.claude-code-compact-sidecar.daemon", result.stdout)
        self.assertIn("plist_removed=yes", result.stdout)
        self.assertIn("launchctl_invoked=no", result.stdout)

    def test_malformed_daemon_state_reports_attention_without_writing_errors_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text("{\n", encoding="utf-8")
            result = self.run_status(runtime_dir)
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("daemon-state.json: present", result.stdout)
        self.assertIn("malformed=yes", result.stdout)
        self.assertIn("status: attention", result.stdout)


if __name__ == "__main__":
    unittest.main()
