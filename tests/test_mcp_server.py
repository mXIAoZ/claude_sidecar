from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.mcp import server as mcp_server


class McpServerTests(unittest.TestCase):
    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
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
        response = self.call_tool(name, arguments)
        self.assertNotIn("error", response)
        text = response["result"]["content"][0]["text"]
        return json.loads(text)

    def subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return env

    def with_runtime(self, runtime_dir: Path):
        class RuntimeContext:
            def __enter__(inner_self):
                inner_self.previous = os.environ.get("SIDECAR_COMPACT_DIR")
                os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
                return runtime_dir

            def __exit__(inner_self, exc_type, exc, tb):
                if inner_self.previous is None:
                    os.environ.pop("SIDECAR_COMPACT_DIR", None)
                else:
                    os.environ["SIDECAR_COMPACT_DIR"] = inner_self.previous

        return RuntimeContext()

    def test_initialize_response(self) -> None:
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual(response["result"]["serverInfo"]["name"], "sidecar-mcp")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tools_list_includes_read_only_tools(self) -> None:
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertIn("sidecar_status", tool_names)
        self.assertIn("sidecar_dashboard", tool_names)
        self.assertIn("sidecar_config_validate", tool_names)
        self.assertIn("sidecar_operation_log", tool_names)

    def test_status_tool_missing_runtime_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            with self.with_runtime(runtime_dir):
                payload = self.tool_payload("sidecar_status")

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["readiness"]["level"], "low")

    def test_dashboard_tool_hides_raw_content_by_default(self) -> None:
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
            with self.with_runtime(runtime_dir):
                hidden = self.tool_payload("sidecar_dashboard")
                shown = self.tool_payload("sidecar_dashboard", {"show_content": True})

        self.assertNotIn("raw", hidden["operations"][0])
        self.assertEqual(shown["operations"][0]["raw"]["prompt"], secret)

    def test_operation_log_tool_hides_raw_content_by_default(self) -> None:
        secret = "RAW_SECRET_SUMMARY"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "operation-log.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "service": "daemon",
                        "operation": "llm-summary",
                        "status": "ok",
                        "metadata": {"summary_chars": len(secret)},
                        "content_policy": {"raw_prompt_logged": False, "raw_summary_logged": True},
                        "raw": {"summary": secret},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.with_runtime(runtime_dir):
                hidden = self.tool_payload("sidecar_operation_log")
                shown = self.tool_payload("sidecar_operation_log", {"show_content": True})

        self.assertNotIn("raw", hidden["records"][0])
        self.assertEqual(shown["records"][0]["raw"]["summary"], secret)

    def test_show_content_must_be_json_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "operation-log.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-05-21T12:00:00+00:00",
                        "service": "controller",
                        "operation": "send-prompt",
                        "status": "ok",
                        "metadata": {},
                        "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                        "raw": {"prompt": "RAW_SECRET_PROMPT"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.with_runtime(runtime_dir):
                response = self.call_tool("sidecar_dashboard", {"show_content": "true"})

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("show_content must be a boolean", response["error"]["message"])

    def test_operation_log_include_rotated_must_be_json_boolean(self) -> None:
        response = self.call_tool("sidecar_operation_log", {"include_rotated": 1})

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("include_rotated must be a boolean", response["error"]["message"])

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "sidecar.config.json"
            config_path.write_text(json.dumps({"llm": {"api_key_env": "SIDECAR_TEST_API_KEY"}}), encoding="utf-8")
            previous = os.environ.get("SIDECAR_TEST_API_KEY")
            os.environ["SIDECAR_TEST_API_KEY"] = "secret-value"
            try:
                payload = self.tool_payload("sidecar_config_validate", {"config_path": str(config_path)})
            finally:
                if previous is None:
                    os.environ.pop("SIDECAR_TEST_API_KEY", None)
                else:
                    os.environ["SIDECAR_TEST_API_KEY"] = previous

        self.assertTrue(payload["valid"])
        self.assertEqual(payload["api_key_env"], "SIDECAR_TEST_API_KEY")
        self.assertNotIn("secret-value", json.dumps(payload))

    def test_unknown_tool_returns_error(self) -> None:
        response = self.call_tool("missing_tool")

        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("unknown tool", response["error"]["message"])

    def test_stdio_smoke(self) -> None:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "compact_sidecar.mcp.server"],
            input=request,
            text=True,
            capture_output=True,
            check=True,
            env=self.subprocess_env(),
        )
        response = json.loads(result.stdout)

        self.assertEqual(response["id"], 1)
        self.assertIn("tools", response["result"])

    def test_self_test_lists_tools(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "compact_sidecar.mcp.server", "--self-test"],
            text=True,
            capture_output=True,
            check=True,
            env=self.subprocess_env(),
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["server"], "sidecar-mcp")
        self.assertIn("sidecar_status", payload["tools"])


if __name__ == "__main__":
    unittest.main()
