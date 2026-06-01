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
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from readiness import READINESS_HIGH_CHARS, READINESS_MEDIUM_CHARS


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
        self.assertIn("compact-readiness: low", result.stdout)
        self.assertIn("estimated_chars=0", result.stdout)
        self.assertIn("basis=local-runtime-file-sizes", result.stdout)
        self.assertIn("accuracy=approximate", result.stdout)
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
        self.assertIn("compact-readiness: low", result.stdout)
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

    def test_large_runtime_files_report_medium_and_high_readiness_without_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            medium_dir = Path(temp_dir) / "medium"
            medium_dir.mkdir()
            medium_text = "M" * READINESS_MEDIUM_CHARS
            (medium_dir / "rolling-summary.draft.md").write_text(medium_text, encoding="utf-8")
            medium_result = self.run_status(medium_dir)

            high_dir = Path(temp_dir) / "high"
            high_dir.mkdir()
            high_text = "H" * READINESS_HIGH_CHARS
            (high_dir / "rolling-summary.draft.md").write_text(high_text, encoding="utf-8")
            high_result = self.run_status(high_dir)

        self.assertIn("compact-readiness: medium", medium_result.stdout)
        self.assertIn(f"estimated_chars={READINESS_MEDIUM_CHARS}", medium_result.stdout)
        self.assertNotIn(medium_text, medium_result.stdout)
        self.assertIn("compact-readiness: high", high_result.stdout)
        self.assertIn(f"estimated_chars={READINESS_HIGH_CHARS}", high_result.stdout)
        self.assertNotIn(high_text, high_result.stdout)

    def test_status_does_not_modify_rolling_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            summary_path = runtime_dir / "rolling-summary.md"
            summary = "## Compact 前必须保留\nkeep me\n"
            summary_path.write_text(summary, encoding="utf-8")

            self.run_status(runtime_dir)

            self.assertEqual(summary_path.read_text(encoding="utf-8"), summary)

    def test_malformed_jsonl_reports_warning_without_writing_errors_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "compact-history.jsonl").write_text("{\n", encoding="utf-8")
            result = self.run_status(runtime_dir)
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("malformed=1", result.stdout)
        self.assertIn("compact-readiness: attention", result.stdout)
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
        self.assertIn("compact-readiness: attention", result.stdout)
        self.assertIn("status: attention", result.stdout)

    def test_operation_log_reports_metadata_without_raw_content(self) -> None:
        secret = "STATUS_HIDDEN_RAW_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_jsonl(
                runtime_dir / "operation-log.jsonl",
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "controller",
                    "operation": "send-prompt",
                    "status": "ok",
                    "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                    "raw": {"prompt": secret},
                },
            )

            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("operation-log.jsonl: present", result.stdout)
        self.assertIn("records=1", result.stdout)
        self.assertIn("raw_prompt_logged=yes", result.stdout)
        self.assertIn("raw_summary_logged=no", result.stdout)
        self.assertNotIn(secret, result.stdout)

    def test_malformed_operation_log_reports_attention_without_writing_errors_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "operation-log.jsonl").write_text("{\n", encoding="utf-8")
            result = self.run_status(runtime_dir)
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("operation-log.jsonl: present", result.stdout)
        self.assertIn("malformed=1", result.stdout)
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

    def test_valid_daemon_state_reports_llm_token_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "run-once",
                        "candidate_count": 2,
                        "llm_summary_status": "ok",
                        "llm_provider": "openai-compatible",
                        "llm_model": "summary-model",
                        "llm_prompt_tokens": 101,
                        "llm_completion_tokens": 202,
                        "llm_total_tokens": 303,
                        "llm_elapsed_ms": 404,
                        "llm_last_success_model": "summary-model",
                        "llm_last_success_total_tokens": 303,
                        "summary_written": str(runtime_dir / "rolling-summary.md"),
                        "summary_backup": str(runtime_dir / "rolling-summary.backup.20260521T120000Z.md"),
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("llm_summary_status=ok", result.stdout)
        self.assertIn("llm_provider=openai-compatible", result.stdout)
        self.assertIn("llm_model=summary-model", result.stdout)
        self.assertIn("llm_prompt_tokens=101", result.stdout)
        self.assertIn("llm_completion_tokens=202", result.stdout)
        self.assertIn("llm_total_tokens=303", result.stdout)
        self.assertIn("llm_elapsed_ms=404", result.stdout)
        self.assertIn("summary_written=", result.stdout)
        self.assertIn("summary_backup=", result.stdout)
        self.assertIn("llm_last_success_model=summary-model", result.stdout)
        self.assertIn("llm_last_success_total_tokens=303", result.stdout)

    def test_llm_error_state_reports_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "run-once",
                        "llm_summary_status": "error",
                        "error_kind": "LLMSummaryRequestError",
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("llm_summary_status=error", result.stdout)
        self.assertIn("error_kind=LLMSummaryRequestError", result.stdout)
        self.assertIn("status: attention", result.stdout)

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

    def test_valid_daemon_state_reports_launchctl_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "mode": "launchctl-bootstrap",
                        "plist_path": str(runtime_dir / "sidecar.plist"),
                        "label": "com.claude-code-compact-sidecar.daemon",
                        "launchctl_invoked": True,
                        "launchctl_action": "bootstrap",
                        "launchctl_target": "gui/501",
                        "launchctl_returncode": 0,
                        "launchctl_status": "ok",
                        "plist_validated": True,
                        "error_kind": "FileNotFoundError",
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_status(runtime_dir)

        self.assertEqual(result.stderr, "")
        self.assertIn("mode=launchctl-bootstrap", result.stdout)
        self.assertIn("launchctl_invoked=yes", result.stdout)
        self.assertIn("launchctl_action=bootstrap", result.stdout)
        self.assertIn("launchctl_target=gui/501", result.stdout)
        self.assertIn("launchctl_returncode=0", result.stdout)
        self.assertIn("launchctl_status=ok", result.stdout)
        self.assertIn("plist_validated=yes", result.stdout)
        self.assertIn("error_kind=FileNotFoundError", result.stdout)

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
