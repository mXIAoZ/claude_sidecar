from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.mcp import server as mcp_server


class McpRehearsalTests(unittest.TestCase):
    def tool_payload(self, name: str, arguments: dict | None = None) -> dict:
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
        self.assertNotIn("error", response)
        return json.loads(response["result"]["content"][0]["text"])

    def test_tools_list_includes_rehearsal_tools(self) -> None:
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertIn("sidecar_setup_rehearsal", tool_names)
        self.assertIn("sidecar_daemon_plist_rehearsal", tool_names)
        self.assertIn("sidecar_daemon_status", tool_names)
        self.assertIn("sidecar_compact_plan_preview", tool_names)

    def test_setup_rehearsal_writes_only_explicit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            runtime_dir = root / "runtime"
            plist_path = root / "sidecar.plist"
            launchctl_path = root / "launchctl-called"
            tmux_path = root / "tmux-called"
            previous_launchctl = os.environ.get("SIDECAR_LAUNCHCTL_PATH")
            previous_tmux = os.environ.get("SIDECAR_TMUX_PATH")
            os.environ["SIDECAR_LAUNCHCTL_PATH"] = str(launchctl_path)
            os.environ["SIDECAR_TMUX_PATH"] = str(tmux_path)
            try:
                payload = self.tool_payload(
                    "sidecar_setup_rehearsal",
                    {"settings_path": str(settings_path), "runtime_dir": str(runtime_dir), "plist_path": str(plist_path)},
                )
            finally:
                if previous_launchctl is None:
                    os.environ.pop("SIDECAR_LAUNCHCTL_PATH", None)
                else:
                    os.environ["SIDECAR_LAUNCHCTL_PATH"] = previous_launchctl
                if previous_tmux is None:
                    os.environ.pop("SIDECAR_TMUX_PATH", None)
                else:
                    os.environ["SIDECAR_TMUX_PATH"] = previous_tmux

            self.assertTrue(payload["ok"])
            self.assertTrue(settings_path.exists())
            self.assertTrue(plist_path.exists())
            self.assertTrue((runtime_dir / "daemon-state.json").exists())
            self.assertFalse(launchctl_path.exists())
            self.assertFalse(tmux_path.exists())
            self.assertIn("launchctl was not invoked", payload["warnings"])
            self.assertIn("tmux was not invoked", payload["warnings"])
            encoded = json.dumps(payload)
            self.assertNotIn("secret-value", encoded)
            self.assertNotIn("raw", encoded)

    def test_daemon_plist_rehearsal_and_status_use_explicit_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            plist_path = root / "sidecar.plist"
            payload = self.tool_payload(
                "sidecar_daemon_plist_rehearsal",
                {"plist_path": str(plist_path), "runtime_dir": str(runtime_dir), "interval_seconds": 5},
            )
            status_payload = self.tool_payload("sidecar_daemon_status", {"plist_path": str(plist_path)})

            self.assertTrue(payload["ok"])
            self.assertTrue(plist_path.exists())
            self.assertEqual(status_payload["exit_code"], 0)
            self.assertIn("status: valid", status_payload["text"])
            self.assertIn(str(plist_path), payload["artifacts"]["plist"]["path"])
            self.assertIn("launchctl was not invoked", payload["warnings"])

    def test_compact_plan_preview_does_not_create_missing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing-runtime"
            payload = self.tool_payload(
                "sidecar_compact_plan_preview",
                {"runtime_dir": str(runtime_dir), "prompt_chars": 123, "min_readiness": "high"},
            )

            self.assertTrue(payload["ok"])
            self.assertFalse(runtime_dir.exists())
            self.assertEqual(payload["artifacts"]["runtime_dir"]["exists"], False)
            self.assertEqual(payload["plan"]["prompt_chars"], 123)
            self.assertIn("tmux was not invoked", payload["warnings"])

    def test_rehearsal_missing_required_path_returns_parameter_error(self) -> None:
        response = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "sidecar_setup_rehearsal", "arguments": {"runtime_dir": "/tmp/runtime"}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("settings_path is required", response["error"]["message"])
    def test_daemon_status_missing_required_path_returns_parameter_error(self) -> None:
        response = mcp_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "sidecar_daemon_status", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("plist_path is required", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
