from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import CONFIG_PATH_ENV, TEMPLATE_NAME, SidecarConfigError, load_config, load_config_safe, load_template, template_path


class SidecarConfigTests(unittest.TestCase):
    def write_config(self, payload: dict) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_path = Path(temp_dir.name) / "sidecar.config.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        return config_path

    def assert_rejects_config(self, payload: dict, expected: str) -> None:
        config_path = self.write_config(payload)
        with self.assertRaises(SidecarConfigError) as context:
            load_config(config_path)
        self.assertIn(expected, str(context.exception))

    def test_template_loads(self) -> None:
        config = load_config()

        self.assertEqual(config["schema_version"], 1)
        self.assertIn("runtime_files", config["paths"])

    def test_unknown_key_is_rejected(self) -> None:
        self.assert_rejects_config({"paths": {"unknown": "value"}}, "unknown config key: paths.unknown")

    def test_runtime_file_names_must_be_basenames(self) -> None:
        self.assert_rejects_config(
            {"paths": {"runtime_files": {"rolling_summary": "../rolling-summary.md"}}},
            "paths.runtime_files.rolling_summary must be a file name",
        )

    def test_script_names_must_be_basenames(self) -> None:
        self.assert_rejects_config(
            {"paths": {"scripts": {"userprompt": "/tmp/userprompt_inject.py"}}},
            "paths.scripts.userprompt must be a file name",
        )
        self.assert_rejects_config(
            {"hooks": {"entries": [{"event": "UserPromptSubmit", "matcher": "", "script": "../hook.py", "timeout": 5, "status_message": "bad"}]}},
            "hooks.entries[0].script must be a file name",
        )

    def test_runtime_file_lists_must_use_basenames(self) -> None:
        self.assert_rejects_config(
            {"readiness": {"runtime_pressure_files": ["../pressure.jsonl"]}},
            "readiness.runtime_pressure_files[0] must be a file name",
        )
        self.assert_rejects_config(
            {"history_candidates": {"history_names": ["nested/history.jsonl"]}},
            "history_candidates.history_names[0] must be a file name",
        )
        self.assert_rejects_config(
            {"dashboard_status": {"known_files_order": ["nested/status.json"]}},
            "dashboard_status.known_files_order[0] must be a file name",
        )

    def test_operation_and_daemon_file_names_must_be_basenames(self) -> None:
        self.assert_rejects_config(
            {"operation_log": {"file_name": "../operation-log.jsonl"}},
            "operation_log.file_name must be a file name",
        )
        self.assert_rejects_config(
            {"daemon_launchd": {"state_file": "/tmp/daemon-state.json"}},
            "daemon_launchd.state_file must be a file name",
        )
        self.assert_rejects_config(
            {"paths": {"summary_backup_prefix": "backup/rolling-summary"}},
            "paths.summary_backup_prefix must be a file name",
        )

    def test_safe_loader_fails_closed_for_env_config(self) -> None:
        config_path = self.write_config({"paths": {"unknown": "value"}})
        with self.assertRaises(SidecarConfigError) as context:
            load_config_safe(environ={CONFIG_PATH_ENV: str(config_path)})
        self.assertIn("unknown config key: paths.unknown", str(context.exception))

    def test_runtime_default_and_project_markers_must_be_basenames(self) -> None:
        self.assert_rejects_config(
            {"paths": {"default_runtime_dir_name": "nested/.memory"}},
            "paths.default_runtime_dir_name must be a file name",
        )
        self.assert_rejects_config(
            {"paths": {"project_root_markers": ["../.git"]}},
            "paths.project_root_markers[0] must be a file name",
        )

    def test_hook_entry_timeout_must_be_positive_integer(self) -> None:
        self.assert_rejects_config(
            {"hooks": {"entries": [{"event": "UserPromptSubmit", "matcher": "", "script": "userprompt_inject.py", "timeout": "bad", "status_message": "status"}]}},
            "config key hooks.entries[0].timeout must be positive",
        )

    def test_llm_persisted_values_must_be_secret_safe(self) -> None:
        self.assert_rejects_config(
            {"llm": {"endpoint": "https://user:pass@example.test/v1/chat/completions"}},
            "llm.endpoint must not include credentials, query, or fragment",
        )
        self.assert_rejects_config(
            {"llm": {"endpoint": "https://example.test/v1/chat/completions?token=secret"}},
            "llm.endpoint must not include credentials, query, or fragment",
        )
        self.assert_rejects_config(
            {"llm": {"api_key_env": "BAD-NAME"}},
            "llm.api_key_env must be an environment variable name",
        )

    def test_plist_file_mode_must_be_owner_only_octal(self) -> None:
        self.assert_rejects_config(
            {"daemon_launchd": {"plist_file_mode": "invalid"}},
            "daemon_launchd.plist_file_mode must be an octal file mode",
        )
        self.assert_rejects_config(
            {"daemon_launchd": {"plist_file_mode": "0644"}},
            "daemon_launchd.plist_file_mode must not grant group or other permissions",
        )

    def test_template_path_loads_from_source_tree(self) -> None:
        self.assertEqual(template_path(), PROJECT_ROOT / TEMPLATE_NAME)

    def test_template_path_falls_back_to_installed_data(self) -> None:
        from . import config as sidecar_config

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            installed_template = temp_path / TEMPLATE_NAME
            installed_template.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            original_project_root = sidecar_config.project_root
            original_prefix = sidecar_config.sys.prefix
            sidecar_config.project_root = lambda: temp_path / "missing-source"
            sidecar_config.sys.prefix = str(temp_path)
            try:
                self.assertEqual(sidecar_config.template_path(), installed_template)
            finally:
                sidecar_config.project_root = original_project_root
                sidecar_config.sys.prefix = original_prefix


if __name__ == "__main__":
    unittest.main()
