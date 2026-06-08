from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.hooks import install as hook_install

MODULE = "compact_sidecar.hooks.install"


class InstallHooksTests(unittest.TestCase):
    def run_installer(self, settings_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", MODULE, "--settings", str(settings_path), *args],
            text=True,
            capture_output=True,
            env=env,
        )

    def load_settings(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def sidecar_hook_count(self, settings: dict, event: str, matcher: str, script_name: str) -> int:
        count = 0
        for entry in settings["hooks"][event]:
            if entry.get("matcher", "") != matcher:
                continue
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if hook.get("type") == "command" and hook_install.command_references_script(command, script_name):
                    count += 1
        return count

    def test_creates_new_settings_file_with_required_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            result = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "userprompt_inject.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "auto", "postcompact_record.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "manual", "postcompact_record.py"), 1)

    def test_preserves_unrelated_settings_and_existing_hooks(self) -> None:
        existing = {
            "permissions": {"allow": ["Bash(git status:*)"]},
            "statusLine": {"type": "command", "command": "status"},
            "enabledPlugins": ["example"],
            "autoCompact": False,
            "customKey": {"keep": True},
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "python3 syntax_check.py", "timeout": 3}
                        ],
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(existing), encoding="utf-8")
            result = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings["permissions"], existing["permissions"])
        self.assertEqual(settings["statusLine"], existing["statusLine"])
        self.assertEqual(settings["enabledPlugins"], existing["enabledPlugins"])
        self.assertEqual(settings["autoCompact"], existing["autoCompact"])
        self.assertEqual(settings["customKey"], existing["customKey"])
        userprompt_hooks = settings["hooks"]["UserPromptSubmit"][0]["hooks"]
        self.assertTrue(any(hook.get("command") == "python3 syntax_check.py" for hook in userprompt_hooks))
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "userprompt_inject.py"), 1)

    def test_installer_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            first = self.run_installer(settings_path)
            second = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "userprompt_inject.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "auto", "postcompact_record.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "manual", "postcompact_record.py"), 1)

    def test_existing_script_basename_counts_as_installed(self) -> None:
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "python3 /old/userprompt_inject.py"}]}
                ],
                "PostCompact": [
                    {"matcher": "auto", "hooks": [{"type": "command", "command": "python3 /old/postcompact_record.py"}]},
                    {"matcher": "manual", "hooks": [{"type": "command", "command": "python3 /old/postcompact_record.py"}]},
                ],
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(existing), encoding="utf-8")
            result = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "userprompt_inject.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "auto", "postcompact_record.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "PostCompact", "manual", "postcompact_record.py"), 1)

    def test_similar_script_name_does_not_count_as_installed(self) -> None:
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "python3 /old/not_userprompt_inject.py"}]}
                ]
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(existing), encoding="utf-8")
            result = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "not_userprompt_inject.py"), 1)
        self.assertEqual(self.sidecar_hook_count(settings, "UserPromptSubmit", "", "userprompt_inject.py"), 1)

    def test_invalid_hooks_shape_fails_without_overwriting(self) -> None:
        original = {"hooks": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            original_text = json.dumps(original)
            settings_path.write_text(original_text, encoding="utf-8")
            result = self.run_installer(settings_path)
            after_text = settings_path.read_text(encoding="utf-8")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("settings.hooks must be a JSON object", result.stderr)
        self.assertEqual(after_text, original_text)

    def test_generated_hook_commands_do_not_start_background_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            result = self.run_installer(settings_path)
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        forbidden_tokens = ("nohup", "launchctl", " &", "& ")
        commands = [
            hook.get("command", "")
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("type") == "command"
        ]
        self.assertTrue(commands)
        for command in commands:
            self.assertFalse(any(token in command for token in forbidden_tokens), command)

    def test_generated_hook_commands_quote_configured_python_executable(self) -> None:
        python_executable = "/tmp/python path; touch /tmp/sidecar-injected"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"python_executable": python_executable}}), encoding="utf-8")
            result = self.run_installer(settings_path, "--config", str(config_path))
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [
            hook["command"]
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("type") == "command"
        ]
        self.assertTrue(commands)
        for command in commands:
            parts = shlex.split(command)
            self.assertIn(python_executable, parts)
            self.assertNotIn("touch", parts)
            self.assertNotIn("/tmp/sidecar-injected", parts)

    def test_invalid_hook_entry_timeout_fails_without_creating_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "entries": [
                                {
                                    "event": "UserPromptSubmit",
                                    "matcher": "",
                                    "script": "userprompt_inject.py",
                                    "timeout": "bad",
                                    "status_message": "status",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_installer(settings_path, "--config", str(config_path))

            self.assertFalse(settings_path.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config key hooks.entries[0].timeout must be positive", result.stderr)

    def test_invalid_config_fails_without_creating_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"unknown": "value"}}), encoding="utf-8")
            result = self.run_installer(settings_path, "--config", str(config_path))

            self.assertFalse(settings_path.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown config key: paths.unknown", result.stderr)

    def test_uninstall_removes_only_sidecar_hooks(self) -> None:
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "python3 /old/userprompt_inject.py"},
                            {"type": "command", "command": "python3 keep.py"},
                        ],
                    }
                ],
                "PostCompact": [
                    {"matcher": "auto", "hooks": [{"type": "command", "command": "python3 /old/postcompact_record.py"}]},
                    {"matcher": "manual", "hooks": [{"type": "command", "command": "python3 /old/postcompact_record.py"}]},
                ],
            },
            "permissions": {"allow": ["Bash(git status:*)"]},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(existing), encoding="utf-8")
            result = self.run_installer(settings_path, "--uninstall")
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Removed 3 sidecar hooks", result.stdout)
        self.assertEqual(settings["permissions"], existing["permissions"])
        self.assertEqual(settings["hooks"], {"UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "python3 keep.py"}]}]})

    def test_uninstall_preserves_same_script_outside_target_matchers(self) -> None:
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "other", "hooks": [{"type": "command", "command": "python3 /old/userprompt_inject.py"}]},
                ],
                "PostCompact": [
                    {"matcher": "custom", "hooks": [{"type": "command", "command": "python3 /old/postcompact_record.py"}]},
                ],
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps(existing), encoding="utf-8")
            result = self.run_installer(settings_path, "--uninstall")
            settings = self.load_settings(settings_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings, existing)
        self.assertIn("Removed 0 sidecar hooks", result.stdout)

    def test_uninstall_missing_settings_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            result = self.run_installer(settings_path, "--uninstall")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(settings_path.exists())
        self.assertIn("Removed 0 sidecar hooks", result.stdout)

    def test_default_settings_help_keeps_confirmation_compatibility_flag(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", MODULE, "--help"],
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

        self.assertIn("--confirm-user-settings", result.stdout)
        self.assertIn("--uninstall", result.stdout)
    def test_script_command_uses_package_module(self) -> None:
        command = hook_install.script_command("userprompt_inject.py")

        parts = shlex.split(command)
        self.assertIn("PYTHONPATH", parts[0])
        self.assertEqual(parts[-3:], [sys.executable, "-m", "compact_sidecar.hooks.userprompt"])

    def test_module_fallback_hook_is_idempotent_and_removable(self) -> None:
        userprompt_command = f"{sys.executable} -m compact_sidecar.hooks.userprompt"
        postcompact_command = f"{sys.executable} -m compact_sidecar.hooks.postcompact"
        settings = {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": userprompt_command}]}
                ],
                "PostCompact": [
                    {"matcher": "auto", "hooks": [{"type": "command", "command": postcompact_command}]},
                    {"matcher": "manual", "hooks": [{"type": "command", "command": postcompact_command}]},
                ],
            }
        }

        merged = hook_install.merge_hooks(json.loads(json.dumps(settings)))
        self.assertEqual(self.sidecar_hook_count(merged, "UserPromptSubmit", "", "userprompt_inject.py"), 1)
        self.assertEqual(len(merged["hooks"]["UserPromptSubmit"][0]["hooks"]), 1)
        self.assertEqual(len(merged["hooks"]["PostCompact"][0]["hooks"]), 1)
        self.assertEqual(len(merged["hooks"]["PostCompact"][1]["hooks"]), 1)

        removed_settings, removed = hook_install.remove_sidecar_hooks(merged)
        self.assertEqual(removed, 3)
        self.assertNotIn("hooks", removed_settings)


if __name__ == "__main__":
    unittest.main()
