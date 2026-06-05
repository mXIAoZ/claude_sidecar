from __future__ import annotations

import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mcp_server


class McpMutationTests(unittest.TestCase):
    def tool_response(self, name: str, arguments: dict | None = None) -> dict:
        response = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        self.assertIsNotNone(response)
        assert response is not None
        return response

    def tool_payload(self, name: str, arguments: dict | None = None) -> dict:
        response = self.tool_response(name, arguments)
        self.assertNotIn("error", response)
        return json.loads(response["result"]["content"][0]["text"])

    def make_fake_launchctl(self, root: Path, *, exit_code: int = 0) -> tuple[Path, Path, dict[str, str | None]]:
        log_path = root / "launchctl-calls.jsonl"
        script_path = root / "fake-launchctl.py"
        script_path.write_text(
            "\n".join(
                [
                    f"#!{sys.executable}",
                    "import json",
                    "import os",
                    "import sys",
                    "with open(os.environ['FAKE_LAUNCHCTL_LOG'], 'a', encoding='utf-8') as handle:",
                    "    handle.write(json.dumps(sys.argv[1:]) + '\\n')",
                    "raise SystemExit(int(os.environ.get('FAKE_LAUNCHCTL_EXIT', '0')))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        previous = {"SIDECAR_LAUNCHCTL_PATH": os.environ.get("SIDECAR_LAUNCHCTL_PATH"), "FAKE_LAUNCHCTL_LOG": os.environ.get("FAKE_LAUNCHCTL_LOG"), "FAKE_LAUNCHCTL_EXIT": os.environ.get("FAKE_LAUNCHCTL_EXIT")}
        os.environ["SIDECAR_LAUNCHCTL_PATH"] = str(script_path)
        os.environ["FAKE_LAUNCHCTL_LOG"] = str(log_path)
        os.environ["FAKE_LAUNCHCTL_EXIT"] = str(exit_code)
        return script_path, log_path, previous

    def restore_env(self, previous: dict[str, str | None]) -> None:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def make_fake_tmux(self, root: Path, *, exit_code: int = 0) -> tuple[Path, Path, dict[str, str | None]]:
        log_path = root / "tmux-calls.jsonl"
        script_path = root / "fake-tmux.py"
        script_path.write_text(
            "\n".join(
                [
                    f"#!{sys.executable}",
                    "import json",
                    "import os",
                    "import sys",
                    "with open(os.environ['FAKE_TMUX_LOG'], 'a', encoding='utf-8') as handle:",
                    "    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')",
                    "raise SystemExit(int(os.environ.get('FAKE_TMUX_EXIT', '0')))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        previous = {"FAKE_TMUX_LOG": os.environ.get("FAKE_TMUX_LOG"), "FAKE_TMUX_EXIT": os.environ.get("FAKE_TMUX_EXIT")}
        os.environ["FAKE_TMUX_LOG"] = str(log_path)
        os.environ["FAKE_TMUX_EXIT"] = str(exit_code)
        return script_path, log_path, previous

    def read_calls(self, log_path: Path) -> list[list[str]]:
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    def read_records(self, runtime_dir: Path) -> list[dict]:
        path = runtime_dir / "operation-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_mutation_tools_are_listed(self) -> None:
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertIn("sidecar_hook_install", tool_names)
        self.assertIn("sidecar_hook_uninstall", tool_names)
        self.assertIn("sidecar_daemon_plist_write", tool_names)
        self.assertIn("sidecar_daemon_plist_remove", tool_names)
        self.assertIn("sidecar_daemon_run_once", tool_names)
        self.assertIn("sidecar_launchctl_lifecycle", tool_names)
        self.assertIn("sidecar_tmux_compact", tool_names)

    def test_mutations_require_confirm_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            response = self.tool_response("sidecar_hook_install", {"settings_path": str(settings_path), "confirm": False})

            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("confirm must be true", response["error"]["message"])
            self.assertFalse(settings_path.exists())

    def test_mutation_boolean_arguments_must_be_json_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            response = self.tool_response("sidecar_hook_install", {"settings_path": str(settings_path), "confirm": "true"})

            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("confirm must be a boolean", response["error"]["message"])
            self.assertFalse(settings_path.exists())

    def test_global_settings_opt_in_must_be_json_boolean(self) -> None:
        response = self.tool_response(
            "sidecar_hook_install",
            {
                "settings_path": str(Path.home() / ".claude" / "settings.json"),
                "confirm": True,
                "allow_global_settings": "true",
            },
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("allow_global_settings must be a boolean", response["error"]["message"])

    def test_tmux_no_send_must_be_json_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            response = self.tool_response(
                "sidecar_tmux_compact",
                {"runtime_dir": str(runtime_dir), "confirm": True, "pane": "session:1.0", "no_send": "false"},
            )

            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("no_send must be a boolean", response["error"]["message"])
            self.assertFalse(runtime_dir.exists())

    def test_global_settings_requires_extra_opt_in(self) -> None:
        response = self.tool_response("sidecar_hook_install", {"settings_path": str(Path.home() / ".claude" / "settings.json"), "confirm": True})

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("allow_global_settings=true", response["error"]["message"])

    def test_hook_install_and_uninstall_modify_explicit_settings_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            install_payload = self.tool_payload("sidecar_hook_install", {"settings_path": str(settings_path), "confirm": True})
            uninstall_payload = self.tool_payload("sidecar_hook_uninstall", {"settings_path": str(settings_path), "confirm": True})
            settings_text = settings_path.read_text(encoding="utf-8")

            self.assertTrue(install_payload["ok"])
            self.assertTrue(uninstall_payload["ok"])
            self.assertIn("settings were written", install_payload["warnings"][0])
            self.assertNotIn("userprompt_inject.py", settings_text)

    def test_plist_write_and_remove_are_confirmed_and_launchctl_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            plist_path = root / "sidecar.plist"
            _, log_path, previous = self.make_fake_launchctl(root)
            try:
                write_payload = self.tool_payload(
                    "sidecar_daemon_plist_write",
                    {"plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "confirm": True, "interval_seconds": 7},
                )
                remove_payload = self.tool_payload(
                    "sidecar_daemon_plist_remove",
                    {"plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "confirm": True},
                )
            finally:
                self.restore_env(previous)

            self.assertTrue(write_payload["ok"])
            self.assertTrue(remove_payload["ok"])
            self.assertFalse(plist_path.exists())
            self.assertEqual(self.read_calls(log_path), [])
            self.assertIn("launchctl was not invoked", write_payload["warnings"])
            self.assertIn("launchctl was not invoked", remove_payload["warnings"])

    def test_launchctl_lifecycle_invokes_fake_after_plist_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            plist_path = root / "sidecar.plist"
            self.tool_payload("sidecar_daemon_plist_write", {"plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "confirm": True})
            _, log_path, previous = self.make_fake_launchctl(root)
            try:
                payload = self.tool_payload(
                    "sidecar_launchctl_lifecycle",
                    {"action": "bootstrap", "plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "confirm": True},
                )
            finally:
                self.restore_env(previous)

            self.assertTrue(payload["ok"])
            self.assertEqual(self.read_calls(log_path), [["bootstrap", f"gui/{os.getuid()}", str(plist_path.resolve())]])
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["launchctl_invoked"])
            self.assertEqual(state["launchctl_action"], "bootstrap")

    def test_launchctl_lifecycle_refuses_invalid_plist_before_fake_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            plist_path = root / "not-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "not.sidecar"}))
            _, log_path, previous = self.make_fake_launchctl(root)
            try:
                payload = self.tool_payload(
                    "sidecar_launchctl_lifecycle",
                    {"action": "bootstrap", "plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "confirm": True},
                )
            finally:
                self.restore_env(previous)

            self.assertFalse(payload["ok"])
            self.assertEqual(self.read_calls(log_path), [])
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            self.assertFalse(state["launchctl_invoked"])
            self.assertEqual(state["launchctl_status"], "refused")

    def test_daemon_run_once_is_bounded_and_metadata_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            payload = self.tool_payload("sidecar_daemon_run_once", {"runtime_dir": str(runtime_dir), "confirm": True, "operation_log": True})
            records = self.read_records(runtime_dir)
            encoded = json.dumps(payload, ensure_ascii=False)

            self.assertTrue(payload["ok"])
            self.assertTrue((runtime_dir / "daemon-state.json").exists())
            self.assertIn("bounded run-once only", payload["warnings"][0])
            self.assertNotIn("raw", encoded)
            for record in records:
                self.assertNotIn("raw", record)

    def test_tmux_compact_no_send_does_not_invoke_fake_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            prompt_path = root / "prompt.txt"
            prompt_path.write_text("do not send", encoding="utf-8")
            tmux_path, log_path, previous = self.make_fake_tmux(root)
            try:
                payload = self.tool_payload(
                    "sidecar_tmux_compact",
                    {
                        "runtime_dir": str(runtime_dir),
                        "confirm": True,
                        "pane": "session:1.0",
                        "prompt_path": str(prompt_path),
                        "tmux_path": str(tmux_path),
                        "no_send": True,
                    },
                )
            finally:
                self.restore_env(previous)

            self.assertTrue(payload["ok"])
            self.assertEqual(self.read_calls(log_path), [])
            self.assertIn("send_disabled=yes", payload["result"]["stdout"])
            self.assertIn("tmux send was disabled", payload["warnings"])

    def test_tmux_compact_send_requires_explicit_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            response = self.tool_response("sidecar_tmux_compact", {"runtime_dir": str(runtime_dir), "confirm": True, "no_send": False})

            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("pane is required", response["error"]["message"])
            self.assertFalse(runtime_dir.exists())

    def test_tmux_compact_send_uses_fake_tmux_and_hides_raw_prompt_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            secret = "SECRET_PROMPT_TEXT"
            prompt_path = root / "prompt.txt"
            prompt_path.write_text(secret, encoding="utf-8")
            tmux_path, log_path, previous = self.make_fake_tmux(root)
            try:
                payload = self.tool_payload(
                    "sidecar_tmux_compact",
                    {
                        "runtime_dir": str(runtime_dir),
                        "confirm": True,
                        "pane": "session:1.0",
                        "prompt_path": str(prompt_path),
                        "tmux_path": str(tmux_path),
                        "no_send": False,
                        "operation_log": True,
                    },
                )
            finally:
                self.restore_env(previous)
            records = self.read_records(runtime_dir)

            self.assertTrue(payload["ok"])
            self.assertEqual(len(self.read_calls(log_path)), 2)
            self.assertIn(secret, json.dumps(self.read_calls(log_path), ensure_ascii=False))
            self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))
            self.assertNotIn(secret, json.dumps(records, ensure_ascii=False))
            for record in records:
                self.assertNotIn("raw", record)

    def test_tmux_raw_prompt_requires_operation_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            response = self.tool_response("sidecar_tmux_compact", {"runtime_dir": str(runtime_dir), "confirm": True, "log_raw_prompt": True})

            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("operation_log_enabled=true", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
