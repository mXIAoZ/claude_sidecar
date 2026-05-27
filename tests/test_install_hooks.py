from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "install_hooks.py"


class InstallHooksTests(unittest.TestCase):
    def run_installer(self, settings_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--settings", str(settings_path), *args],
            text=True,
            capture_output=True,
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
                try:
                    parts = shlex.split(command)
                except ValueError:
                    parts = []
                if hook.get("type") == "command" and any(Path(part).name == script_name for part in parts):
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


if __name__ == "__main__":
    unittest.main()
