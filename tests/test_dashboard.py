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
    def run_dashboard(self, runtime_dir: Path, *args: str, set_runtime_env: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if set_runtime_env:
            env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        else:
            env.pop("SIDECAR_COMPACT_DIR", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
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

    def test_config_sets_runtime_dir_without_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "configured-runtime"
            runtime_dir.mkdir()
            (runtime_dir / "rolling-summary.md").write_text("## Compact 前必须保留\nkeep", encoding="utf-8")
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"runtime_dir": str(runtime_dir)}}), encoding="utf-8")

            result = self.run_dashboard(runtime_dir, "--config", str(config_path), "--no-color", set_runtime_env=False)

        self.assertEqual(result.stderr, "")
        self.assertIn(f"runtime_dir: {runtime_dir}", result.stdout)
        self.assertIn("rolling-summary.md: present", result.stdout)

    def test_config_sets_runtime_file_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "configured-runtime"
            runtime_dir.mkdir()
            (runtime_dir / "custom-summary.md").write_text("## Compact 前必须保留\nkeep", encoding="utf-8")
            with (runtime_dir / "custom-operation-log.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-05-21T12:00:00+00:00",
                            "service": "controller",
                            "operation": "send-prompt",
                            "status": "ok",
                            "metadata": {"prompt_chars": 10},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {
                            "runtime_dir": str(runtime_dir),
                            "runtime_files": {"rolling_summary": "custom-summary.md"},
                        },
                        "operation_log": {"file_name": "custom-operation-log.jsonl"},
                        "dashboard_status": {"known_files_order": ["custom-summary.md", "custom-operation-log.jsonl"]},
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_dashboard(runtime_dir, "--config", str(config_path), "--no-color", set_runtime_env=False)

        self.assertEqual(result.stderr, "")
        self.assertIn("custom-summary.md: present", result.stdout)
        self.assertIn("custom-operation-log.jsonl: present", result.stdout)
        self.assertIn("controller | send-prompt | ok", result.stdout)
        self.assertNotIn("rolling-summary.md: present", result.stdout)
        self.assertTrue(all(not line.startswith("operation-log.jsonl: present") for line in result.stdout.splitlines()))

    def test_invalid_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"unknown": "value"}}), encoding="utf-8")

            result = self.run_dashboard(runtime_dir, "--config", str(config_path), check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown config key: paths.unknown", result.stderr)
        self.assertEqual(result.stdout, "")

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

    def test_dashboard_highlights_latest_llm_token_usage(self) -> None:
        secret_summary = "RAW_LLM_SUMMARY_SHOULD_HIDE"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_operation(
                runtime_dir,
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "daemon",
                    "operation": "llm-summary",
                    "status": "ok",
                    "metadata": {
                        "provider": "openai-compatible",
                        "model": "summary-model",
                        "prompt_tokens": 101,
                        "completion_tokens": 202,
                        "total_tokens": 303,
                        "elapsed_ms": 404,
                    },
                    "content_policy": {"raw_prompt_logged": False, "raw_summary_logged": False},
                    "raw": {"summary": secret_summary},
                },
            )

            result = self.run_dashboard(runtime_dir, "--no-color")

        self.assertIn("LLM Summary", result.stdout)
        self.assertIn("status=ok", result.stdout)
        self.assertIn("provider=openai-compatible", result.stdout)
        self.assertIn("model=summary-model", result.stdout)
        self.assertIn("prompt_tokens=101", result.stdout)
        self.assertIn("completion_tokens=202", result.stdout)
        self.assertIn("total_tokens=303", result.stdout)
        self.assertIn("elapsed_ms=404", result.stdout)
        self.assertNotIn(secret_summary, result.stdout)

    def test_dashboard_prefers_newer_daemon_state_over_stale_llm_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_operation(
                runtime_dir,
                {
                    "timestamp": "2026-05-21T12:00:00+00:00",
                    "service": "daemon",
                    "operation": "llm-summary",
                    "status": "ok",
                    "metadata": {
                        "model": "old-model",
                        "total_tokens": 111,
                    },
                    "content_policy": {"raw_prompt_logged": False, "raw_summary_logged": False},
                },
            )
            (runtime_dir / "daemon-state.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:05:00+00:00",
                        "mode": "run-once",
                        "llm_summary_status": "ok",
                        "llm_model": "new-model",
                        "llm_total_tokens": 222,
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_dashboard(runtime_dir, "--no-color")

        llm_section = result.stdout.split("Recent Operations", 1)[0]
        self.assertIn("model=new-model", llm_section)
        self.assertIn("total_tokens=222", llm_section)
        self.assertNotIn("model=old-model", llm_section)

    def test_dashboard_warns_on_llm_error_state(self) -> None:
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

            result = self.run_dashboard(runtime_dir, "--no-color")

        self.assertIn("status: attention", result.stdout)
        self.assertIn("status=error", result.stdout)
        self.assertIn("daemon LLM summary failed", result.stdout)

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
