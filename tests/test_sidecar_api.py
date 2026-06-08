from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar import api as sidecar_api


class SidecarApiTests(unittest.TestCase):
    def test_status_snapshot_missing_runtime_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            previous = os.environ.get("SIDECAR_COMPACT_DIR")
            os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
            try:
                snapshot = sidecar_api.status_snapshot()
            finally:
                if previous is None:
                    os.environ.pop("SIDECAR_COMPACT_DIR", None)
                else:
                    os.environ["SIDECAR_COMPACT_DIR"] = previous

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(snapshot["status"], "empty")
        self.assertEqual(snapshot["readiness"]["level"], "low")
        self.assertIn("rolling-summary.md", snapshot["files"])

    def test_dashboard_snapshot_hides_raw_content_by_default(self) -> None:
        secret = "RAW_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "operation-log.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "service": "controller",
                        "operation": "send-prompt",
                        "status": "ok",
                        "metadata": {"prompt_chars": len(secret)},
                        "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                        "raw": {"prompt": secret},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            previous = os.environ.get("SIDECAR_COMPACT_DIR")
            os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
            try:
                hidden = sidecar_api.dashboard_snapshot()
                shown = sidecar_api.dashboard_snapshot(show_content=True)
            finally:
                if previous is None:
                    os.environ.pop("SIDECAR_COMPACT_DIR", None)
                else:
                    os.environ["SIDECAR_COMPACT_DIR"] = previous

        self.assertNotIn("raw", hidden["operations"][0])
        self.assertEqual(shown["operations"][0]["raw"]["prompt"], secret)

    def test_validate_config_reports_safe_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "sidecar.config.json"
            config_path.write_text(
                json.dumps({"llm": {"api_key_env": "SIDECAR_TEST_API_KEY"}}),
                encoding="utf-8",
            )
            previous = os.environ.get("SIDECAR_TEST_API_KEY")
            os.environ["SIDECAR_TEST_API_KEY"] = "secret-value"
            try:
                result = sidecar_api.validate_config(config_path)
            finally:
                if previous is None:
                    os.environ.pop("SIDECAR_TEST_API_KEY", None)
                else:
                    os.environ["SIDECAR_TEST_API_KEY"] = previous

        self.assertTrue(result["valid"])
        self.assertEqual(result["api_key_env"], "SIDECAR_TEST_API_KEY")
        self.assertNotIn("secret-value", json.dumps(result))

    def test_validate_config_returns_error_for_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"unknown": "value"}}), encoding="utf-8")
            result = sidecar_api.validate_config(config_path)

        self.assertFalse(result["valid"])
        self.assertIn("unknown config key: paths.unknown", result["error"])

    def test_hook_setup_preview_does_not_write_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"claude_settings_path": str(settings_path)}}), encoding="utf-8")

            result = sidecar_api.hook_setup_preview(config_path)

            self.assertFalse(settings_path.exists())

        self.assertTrue(result["valid"])
        self.assertEqual(result["settings_path"], str(settings_path))
        self.assertGreaterEqual(len(result["hooks"]), 1)
        self.assertIn("command", result["hooks"][0])

    def test_daemon_agent_status_missing_plist_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plist_path = Path(temp_dir) / "sidecar.plist"

            result = sidecar_api.daemon_agent_status(plist_path)

            self.assertFalse(plist_path.exists())

        self.assertEqual(result["exit_code"], 0)
        self.assertIn("plist: absent", result["text"])


if __name__ == "__main__":
    unittest.main()
