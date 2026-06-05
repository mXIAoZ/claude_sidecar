from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]

class FakeLLMHandler(BaseHTTPRequestHandler):
    response_payload: dict = {}
    request_payloads: list[dict] = []
    request_headers: list[dict[str, str]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.request_payloads.append(json.loads(body.decode("utf-8")))
        self.__class__.request_headers.append(dict(self.headers.items()))
        response = ("data: " + json.dumps(self.__class__.response_payload, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def fake_llm_server(payload: dict) -> Iterator[tuple[str, type[FakeLLMHandler]]]:
    FakeLLMHandler.response_payload = payload
    FakeLLMHandler.request_payloads = []
    FakeLLMHandler.request_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1/chat/completions", FakeLLMHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


SCRIPT = PROJECT_ROOT / "src" / "daemon.py"


class DaemonRunOnceTests(unittest.TestCase):
    def run_daemon(
        self,
        runtime_dir: Path,
        *args: str,
        check: bool = True,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        if env_overrides is not None:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            text=True,
            capture_output=True,
            env=env,
        )

    def make_fake_launchctl(self, temp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path, dict[str, str]]:
        log_path = temp_path / "launchctl-calls.jsonl"
        script_path = temp_path / "fake-launchctl.py"
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
        return script_path, log_path, {
            "SIDECAR_LAUNCHCTL_PATH": str(script_path),
            "FAKE_LAUNCHCTL_LOG": str(log_path),
            "FAKE_LAUNCHCTL_EXIT": str(exit_code),
        }

    def read_launchctl_calls(self, log_path: Path) -> list[list[str]]:
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    def write_history_record(self, path: Path, summary: str) -> None:
        record = {
            "timestamp": "2026-05-21T10:00:00+00:00",
            "payload": {"summary": summary},
        }
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    def read_operation_records(self, runtime_dir: Path) -> list[dict]:
        path = runtime_dir / "operation-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_run_once_writes_llm_summary_and_token_metadata(self) -> None:
        compact_summary = "daemon compact summary for llm"
        llm_summary = "# Rolling Summary\n\n## Compact 前必须保留\nllm keep\n"
        payload = {
            "choices": [{"delta": {"content": llm_summary}}],
            "usage": {"prompt_tokens": 101, "completion_tokens": 202, "total_tokens": 303},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)
            with fake_llm_server(payload) as (endpoint, handler):
                result = self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    "--operation-log",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            rolling_summary = (runtime_dir / "rolling-summary.md").read_text(encoding="utf-8")
            state_text = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")
            state = json.loads(state_text)
            records = self.read_operation_records(runtime_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rolling_summary, llm_summary)
        self.assertEqual(state["llm_summary_status"], "ok")
        self.assertEqual(state["llm_prompt_tokens"], 101)
        self.assertEqual(state["llm_completion_tokens"], 202)
        self.assertEqual(state["llm_total_tokens"], 303)
        self.assertTrue(state["summary_written"].endswith("rolling-summary.md"))
        self.assertNotIn(compact_summary, state_text)
        self.assertNotIn(llm_summary, state_text)
        self.assertNotIn("secret-key", state_text)
        self.assertIn("llm_total_tokens: 303", result.stdout)
        self.assertEqual(handler.request_headers[0]["Authorization"], "Bearer secret-key")
        llm_record = next(record for record in records if record["operation"] == "llm-summary")
        run_record = next(record for record in records if record["operation"] == "run-once")
        self.assertEqual(llm_record["metadata"]["total_tokens"], 303)
        self.assertEqual(run_record["metadata"]["llm_total_tokens"], 303)
        self.assertNotIn(compact_summary, json.dumps(llm_record, ensure_ascii=False))
        self.assertNotIn(llm_summary, json.dumps(llm_record, ensure_ascii=False))

    def test_run_once_without_history_skips_llm_and_does_not_write_summary(self) -> None:
        payload = {"choices": [{"delta": {"content": "should not be requested"}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            with fake_llm_server(payload) as (endpoint, handler):
                result = self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((runtime_dir / "rolling-summary.md").exists())
        self.assertEqual(handler.request_payloads, [])
        self.assertEqual(state["llm_summary_status"], "skipped")
        self.assertEqual(state["llm_summary_skipped"], "no_candidates")

    def test_run_once_llm_failure_does_not_overwrite_existing_summary(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            summary_path = runtime_dir / "rolling-summary.md"
            summary_path.write_text(old_summary, encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "new compact summary")

            result = self.run_daemon(
                runtime_dir,
                "--run-once",
                check=False,
                env_overrides={
                    "SIDECAR_LLM_ENDPOINT": "http://127.0.0.1:9/v1/chat/completions",
                    "SIDECAR_LLM_MODEL": "summary-model",
                    "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                    "SIDECAR_TEST_KEY": "secret-key",
                },
            )
            rolling_summary = summary_path.read_text(encoding="utf-8")
            state_text = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")
            state = json.loads(state_text)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(rolling_summary, old_summary)
        self.assertEqual(state["llm_summary_status"], "error")
        self.assertIn("error_kind", state)
        self.assertNotIn("error_message", state)
        self.assertNotIn("secret-key", state_text)

    def test_run_once_uses_configured_rolling_summary_file_and_marker(self) -> None:
        compact_summary = "configured rolling summary input"
        llm_summary = "# Custom Summary\n\n## Keep\nconfigured output\n"
        payload = {"choices": [{"delta": {"content": llm_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {
                            "runtime_dir": str(runtime_dir),
                            "runtime_files": {"rolling_summary": "custom-summary.md"},
                            "summary_backup_prefix": "custom-summary.backup",
                        },
                        "summary": {"required_heading": "# Custom Summary", "required_marker": "## Keep"},
                    }
                ),
                encoding="utf-8",
            )
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            with fake_llm_server(payload) as (endpoint, _):
                result = self.run_daemon(
                    runtime_dir,
                    "--config",
                    str(config_path),
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            custom_summary = (runtime_dir / "custom-summary.md").read_text(encoding="utf-8")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(custom_summary, llm_summary)
        self.assertFalse((runtime_dir / "rolling-summary.md").exists())
        self.assertTrue(state["summary_written"].endswith("custom-summary.md"))

    def test_run_once_uses_configured_rolling_summary_backup_prefix(self) -> None:
        old_summary = "# Custom Summary\n\n## Keep\nold\n"
        new_summary = "# Custom Summary\n\n## Keep\nnew\n"
        payload = {"choices": [{"delta": {"content": new_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {
                            "runtime_dir": str(runtime_dir),
                            "runtime_files": {"rolling_summary": "custom-summary.md"},
                            "summary_backup_prefix": "custom-summary.backup",
                        },
                        "summary": {"required_heading": "# Custom Summary", "required_marker": "## Keep"},
                    }
                ),
                encoding="utf-8",
            )
            summary_path = runtime_dir / "custom-summary.md"
            summary_path.write_text(old_summary, encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "new compact summary")

            with fake_llm_server(payload) as (endpoint, _):
                result = self.run_daemon(
                    runtime_dir,
                    "--config",
                    str(config_path),
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            backups = list(runtime_dir.glob("custom-summary.backup.*.md"))
            backup_text = backups[0].read_text(encoding="utf-8") if backups else ""
            updated_summary = summary_path.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(updated_summary, new_summary)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_text, old_summary)

    def test_run_once_writes_draft_and_metadata_from_history(self) -> None:
        compact_summary = "daemon compact summary from src/daemon.py"
        llm_summary = "# Rolling Summary\n\n## Compact 前必须保留\nfrom llm\n"
        payload = {"choices": [{"delta": {"content": llm_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            with fake_llm_server(payload) as (endpoint, _):
                result = self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon run-once", result.stdout)
        self.assertIn("candidate_count: 1", result.stdout)
        self.assertIn(compact_summary, draft)
        self.assertEqual(state["mode"], "run-once")
        self.assertEqual(state["candidate_count"], 1)
        self.assertTrue(state["draft_written"])
        self.assertIn("timestamp", state)
        self.assertTrue(state["draft_path"].endswith("rolling-summary.draft.md"))

    def test_run_once_counts_unique_deduped_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            history_path = runtime_dir / "compact-history.jsonl"
            history_path.write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False) + "\n"
                    for record in [
                        {"timestamp": "2026-05-21T12:00:00+00:00", "payload": {"summary": "duplicate summary"}},
                        {"timestamp": "2026-05-21T11:00:00+00:00", "payload": {"summary": "duplicate   summary"}},
                        {"timestamp": "2026-05-21T10:00:00+00:00", "payload": {"summary": "unique summary"}},
                    ]
                ),
                encoding="utf-8",
            )

            payload = {"choices": [{"delta": {"content": "# Rolling Summary\n\n## Compact 前必须保留\ndeduped\n"}}]}
            with fake_llm_server(payload) as (endpoint, _):
                result = self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertIn("candidate_count: 2", result.stdout)
        self.assertEqual(state["candidate_count"], 2)
        self.assertEqual(draft.count("duplicate summary"), 1)
        self.assertNotIn("duplicate   summary", draft)
        self.assertIn("unique summary", draft)


    def test_run_once_with_no_history_writes_empty_draft_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_daemon(runtime_dir, "--run-once")
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stderr, "")
        self.assertIn("No compact history summaries found.", draft)
        self.assertEqual(state["candidate_count"], 0)

    def test_run_once_overwrites_rolling_summary_after_backup(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold\n"
        new_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnew\n"
        payload = {"choices": [{"delta": {"content": new_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            rolling_summary_path = runtime_dir / "rolling-summary.md"
            rolling_summary_path.write_text(old_summary, encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "new compact summary")

            with fake_llm_server(payload) as (endpoint, _):
                self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            rolling_summary = rolling_summary_path.read_text(encoding="utf-8")
            backups = list(runtime_dir.glob("rolling-summary.backup.*.md"))
            backup_text = backups[0].read_text(encoding="utf-8") if backups else ""

        self.assertEqual(rolling_summary, new_summary)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_text, old_summary)

    def test_daemon_state_does_not_store_raw_summary_text(self) -> None:
        compact_summary = "do not persist this raw compact body"
        llm_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnot in state\n"
        payload = {"choices": [{"delta": {"content": llm_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            with fake_llm_server(payload) as (endpoint, _):
                self.run_daemon(
                    runtime_dir,
                    "--run-once",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            state_text = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")

        self.assertNotIn(compact_summary, state_text)
        self.assertNotIn(llm_summary, state_text)

    def test_run_once_logs_history_parse_errors_as_daemon_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "compact-history.jsonl").write_text("{\n", encoding="utf-8")

            result = self.run_daemon(runtime_dir, "--run-once")
            errors = (runtime_dir / "errors.log").read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.stderr, "")
        self.assertEqual(len(errors), 1)
        error = json.loads(errors[0])
        self.assertEqual(error["service"], "daemon")
        self.assertIn("failed to parse compact-history.jsonl line", error["message"])

    def test_run_once_writes_only_expected_files_without_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.run_daemon(runtime_dir, "--run-once")
            written_files = sorted(path.name for path in runtime_dir.iterdir())

        self.assertEqual(written_files, ["daemon-state.json", "rolling-summary.draft.md"])

    def test_without_run_once_fails_without_creating_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            result = self.run_daemon(runtime_dir, check=False)

            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--run-once", result.stderr)

    def plist_from_stdout(self, output: str) -> dict:
        start = output.index("<?xml")
        return plistlib.loads(output[start:].encode("utf-8"))

    def test_loop_with_max_runs_updates_metadata_and_exits(self) -> None:
        compact_summary = "loop compact summary from history"
        payload = {"choices": [{"delta": {"content": "# Rolling Summary\n\n## Compact 前必须保留\nloop llm\n"}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            with fake_llm_server(payload) as (endpoint, _):
                result = self.run_daemon(
                    runtime_dir,
                    "--loop",
                    "--interval-seconds",
                    "1",
                    "--max-runs",
                    "2",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon loop", result.stdout)
        self.assertIn(compact_summary, draft)
        self.assertEqual(state["mode"], "loop")
        self.assertEqual(state["interval_seconds"], 1)
        self.assertEqual(state["run_count"], 2)
        self.assertEqual(state["shutdown_reason"], "max-runs")
        self.assertNotIn(compact_summary, json.dumps(state))

    def test_loop_overwrites_rolling_summary_after_backup(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold loop\n"
        new_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnew loop\n"
        payload = {"choices": [{"delta": {"content": new_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            rolling_summary_path = runtime_dir / "rolling-summary.md"
            rolling_summary_path.write_text(old_summary, encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "loop summary")

            with fake_llm_server(payload) as (endpoint, _):
                self.run_daemon(
                    runtime_dir,
                    "--loop",
                    "--interval-seconds",
                    "1",
                    "--max-runs",
                    "1",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            rolling_summary = rolling_summary_path.read_text(encoding="utf-8")
            backups = list(runtime_dir.glob("rolling-summary.backup.*.md"))
            backup_text = backups[0].read_text(encoding="utf-8") if backups else ""

        self.assertEqual(rolling_summary, new_summary)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_text, old_summary)

    def test_loop_skips_llm_when_history_is_unchanged(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold loop\n"
        new_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnew loop\n"
        payload = {"choices": [{"delta": {"content": new_summary}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            rolling_summary_path = runtime_dir / "rolling-summary.md"
            rolling_summary_path.write_text(old_summary, encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "loop unchanged summary")

            with fake_llm_server(payload) as (endpoint, handler):
                self.run_daemon(
                    runtime_dir,
                    "--loop",
                    "--interval-seconds",
                    "1",
                    "--max-runs",
                    "2",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            backups = list(runtime_dir.glob("rolling-summary.backup.*.md"))

        self.assertEqual(len(handler.request_payloads), 1)
        self.assertEqual(len(backups), 1)
        self.assertEqual(state["llm_summary_status"], "skipped")
        self.assertEqual(state["llm_summary_skipped"], "unchanged")
        self.assertNotIn("llm_total_tokens", state)
        self.assertEqual(state["llm_last_success_model"], "summary-model")
        self.assertIn("llm_last_success_total_tokens", state)

    def test_loop_operation_log_records_llm_summary_each_pass(self) -> None:
        payload = {"choices": [{"delta": {"content": "# Rolling Summary\n\n## Compact 前必须保留\nloop log\n"}}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", "loop operation log summary")

            with fake_llm_server(payload) as (endpoint, handler):
                self.run_daemon(
                    runtime_dir,
                    "--loop",
                    "--interval-seconds",
                    "1",
                    "--max-runs",
                    "2",
                    "--operation-log",
                    env_overrides={
                        "SIDECAR_LLM_ENDPOINT": endpoint,
                        "SIDECAR_LLM_MODEL": "summary-model",
                        "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                        "SIDECAR_TEST_KEY": "secret-key",
                    },
                )
            records = self.read_operation_records(runtime_dir)
            llm_records = [record for record in records if record["operation"] == "llm-summary"]

        self.assertEqual(len(handler.request_payloads), 1)
        self.assertEqual(len(llm_records), 2)
        skipped_record = next(record for record in llm_records if record["status"] == "skipped")
        ok_record = next(record for record in llm_records if record["status"] == "ok")
        self.assertEqual(skipped_record["metadata"].get("skipped"), "unchanged")
        self.assertNotIn("total_tokens", skipped_record["metadata"])
        self.assertIn("last_success_total_tokens", skipped_record["metadata"])
        self.assertEqual(ok_record["metadata"].get("model"), "summary-model")
        self.assertNotIn("loop operation log summary", json.dumps(llm_records, ensure_ascii=False))

    def test_invalid_interval_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            result = self.run_daemon(runtime_dir, "--loop", "--interval-seconds", "0", check=False)

            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("interval-seconds must be positive", result.stderr)

    def test_install_agent_requires_explicit_plist_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            result = self.run_daemon(runtime_dir, "--install-agent", check=False)

            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--plist-path is required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_install_agent_writes_plist_to_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                "--interval-seconds",
                "120",
            )
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            plist_mode = plist_path.stat().st_mode & 0o777

        self.assertEqual(result.stderr, "")
        self.assertIn("Wrote launchd plist", result.stdout)
        self.assertEqual(plist_mode, 0o600)
        self.assertEqual(plist["Label"], "com.claude-code-compact-sidecar.daemon")
        self.assertTrue(plist["StandardOutPath"].endswith("daemon.out.log"))
        self.assertTrue(plist["StandardErrorPath"].endswith("daemon.err.log"))
        self.assertEqual(plist["EnvironmentVariables"]["SIDECAR_COMPACT_DIR"], str(runtime_dir))
        self.assertEqual(plist["WorkingDirectory"], str(PROJECT_ROOT))
        program_arguments = " ".join(plist["ProgramArguments"])
        self.assertIn("daemon.py", program_arguments)
        self.assertIn("--operation-log", plist["ProgramArguments"])

    def test_install_agent_does_not_persist_ambient_api_key_without_llm_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                env_overrides={"OPENAI_API_KEY": "ambient-secret"},
            )
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            environment = plist["EnvironmentVariables"]

        self.assertEqual(result.stderr, "")
        self.assertEqual(environment["SIDECAR_COMPACT_DIR"], str(runtime_dir))
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("ambient-secret", json.dumps(plist))

    def test_install_agent_carries_llm_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                env_overrides={
                    "SIDECAR_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
                    "SIDECAR_LLM_MODEL": "summary-model",
                    "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                    "SIDECAR_TEST_KEY": "secret-key",
                    "SIDECAR_LLM_TIMEOUT_SECONDS": "12",
                    "SIDECAR_LLM_MAX_INPUT_CHARS": "1234",
                    "SIDECAR_LLM_MAX_OUTPUT_CHARS": "567",
                },
            )
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            environment = plist["EnvironmentVariables"]

        self.assertEqual(result.stderr, "")
        self.assertEqual(environment["SIDECAR_COMPACT_DIR"], str(runtime_dir))
        self.assertEqual(environment["SIDECAR_LLM_ENDPOINT"], "https://llm.example.test/v1/chat/completions")
        self.assertEqual(environment["SIDECAR_LLM_MODEL"], "summary-model")
        self.assertEqual(environment["SIDECAR_LLM_API_KEY_ENV"], "SIDECAR_TEST_KEY")
        self.assertNotIn("SIDECAR_TEST_KEY", environment)
        self.assertNotIn("secret-key", json.dumps(plist))
        self.assertEqual(environment["SIDECAR_LLM_TIMEOUT_SECONDS"], "12")
        self.assertEqual(environment["SIDECAR_LLM_MAX_INPUT_CHARS"], "1234")
        self.assertEqual(environment["SIDECAR_LLM_MAX_OUTPUT_CHARS"], "567")

    def test_install_agent_does_not_persist_llm_env_alias_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"environment": {"llm_endpoint": "OPENAI_API_KEY"}}), encoding="utf-8")

            result = self.run_daemon(
                runtime_dir,
                "--config",
                str(config_path),
                "--install-agent",
                "--plist-path",
                str(plist_path),
                env_overrides={"OPENAI_API_KEY": "ambient-secret"},
            )
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            environment = plist["EnvironmentVariables"]

        self.assertEqual(result.stderr, "")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("ambient-secret", json.dumps(plist))

    def test_install_agent_rejects_secret_bearing_llm_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                check=False,
                env_overrides={"SIDECAR_LLM_ENDPOINT": "https://example.test/v1/chat/completions?token=secret"},
            )

            self.assertFalse(plist_path.exists())
            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("llm.endpoint must not include credentials, query, or fragment", result.stderr)

    def test_install_agent_rejects_invalid_llm_api_key_env_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                check=False,
                env_overrides={"SIDECAR_LLM_API_KEY_ENV": "BAD-NAME"},
            )

            self.assertFalse(plist_path.exists())
            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("llm.api_key_env must be an environment variable name", result.stderr)

    def test_install_agent_rejects_invalid_llm_numeric_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_daemon(
                runtime_dir,
                "--install-agent",
                "--plist-path",
                str(plist_path),
                check=False,
                env_overrides={"SIDECAR_LLM_TIMEOUT_SECONDS": "bad"},
            )

            self.assertFalse(plist_path.exists())
            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SIDECAR_LLM_TIMEOUT_SECONDS has invalid value", result.stderr)

    def test_run_once_operation_log_uses_configured_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {"runtime_dir": str(runtime_dir)},
                        "operation_log": {"file_name": "custom-operation-log.jsonl"},
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_daemon(
                runtime_dir,
                "--config",
                str(config_path),
                "--run-once",
                "--operation-log",
            )
            custom_log_exists = (runtime_dir / "custom-operation-log.jsonl").exists()
            default_log_exists = (runtime_dir / "operation-log.jsonl").exists()

        self.assertEqual(result.stderr, "")
        self.assertTrue(custom_log_exists)
        self.assertFalse(default_log_exists)

    def test_install_agent_invalid_config_fails_without_writing_plist_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"unknown": "value"}}), encoding="utf-8")

            result = self.run_daemon(
                runtime_dir,
                "--config",
                str(config_path),
                "--install-agent",
                "--plist-path",
                str(plist_path),
                check=False,
            )

            self.assertFalse(plist_path.exists())
            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown config key: paths.unknown", result.stderr)

    def test_agent_status_missing_plist_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "missing.plist"
            result = self.run_daemon(runtime_dir, "--agent-status", "--plist-path", str(plist_path))

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon agent-status", result.stdout)
        self.assertIn("plist: absent", result.stdout)
        self.assertIn("status: absent", result.stdout)

    def test_agent_status_reports_valid_generated_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path), "--interval-seconds", "120")
            result = self.run_daemon(runtime_dir, "--agent-status", "--plist-path", str(plist_path))

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon agent-status", result.stdout)
        self.assertIn("plist: present", result.stdout)
        self.assertIn("label_match=yes", result.stdout)
        self.assertIn("program_daemon=yes", result.stdout)
        self.assertIn("program_loop=yes", result.stdout)
        self.assertIn("program_interval=yes", result.stdout)
        self.assertIn(f"runtime_dir={runtime_dir}", result.stdout)
        self.assertIn("run_at_load=no", result.stdout)
        self.assertIn("keep_alive=no", result.stdout)
        self.assertIn("status: valid", result.stdout)

    def test_agent_status_malformed_plist_reports_invalid_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            plist_path.write_text("not plist", encoding="utf-8")

            result = self.run_daemon(runtime_dir, "--agent-status", "--plist-path", str(plist_path), check=False)

            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Sidecar daemon agent-status", result.stdout)
        self.assertIn("plist: present", result.stdout)
        self.assertIn("status: invalid", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_install_agent_writes_metadata_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path), "--interval-seconds", "120")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(state["mode"], "install-agent")
        self.assertEqual(state["plist_path"], str(plist_path))
        self.assertEqual(state["label"], "com.claude-code-compact-sidecar.daemon")
        self.assertEqual(state["interval_seconds"], 120)
        self.assertFalse(state["launchctl_invoked"])
        self.assertNotIn("compact summary", json.dumps(state))

    def test_remove_agent_missing_plist_exits_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "missing.plist"
            result = self.run_daemon(runtime_dir, "--remove-agent", "--plist-path", str(plist_path))
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon remove-agent", result.stdout)
        self.assertIn("plist: absent", result.stdout)
        self.assertFalse(state["plist_removed"])
        self.assertFalse(state["launchctl_invoked"])

    def test_remove_agent_removes_generated_sidecar_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(runtime_dir, "--remove-agent", "--plist-path", str(plist_path))
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

            self.assertFalse(plist_path.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("plist_removed: yes", result.stdout)
        self.assertTrue(state["plist_removed"])
        self.assertFalse(state["launchctl_invoked"])

    def test_remove_agent_preserves_non_sidecar_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "not-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "not.sidecar"}))

            result = self.run_daemon(runtime_dir, "--remove-agent", "--plist-path", str(plist_path), check=False)
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

            self.assertTrue(plist_path.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plist_removed: no", result.stdout)
        self.assertIn("status: refused", result.stdout)
        self.assertFalse(state["plist_removed"])

    def test_remove_agent_preserves_invalid_sidecar_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "invalid-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "com.claude-code-compact-sidecar.daemon"}))

            result = self.run_daemon(runtime_dir, "--remove-agent", "--plist-path", str(plist_path), check=False)
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

            self.assertTrue(plist_path.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plist_removed: no", result.stdout)
        self.assertIn("status: refused", result.stdout)
        self.assertFalse(state["plist_removed"])

    def test_remove_agent_preserves_malformed_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "bad.plist"
            plist_path.write_text("not plist", encoding="utf-8")

            result = self.run_daemon(runtime_dir, "--remove-agent", "--plist-path", str(plist_path), check=False)
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

            self.assertTrue(plist_path.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plist_removed: no", result.stdout)
        self.assertIn("status: invalid", result.stdout)
        self.assertFalse(state["plist_removed"])


    def test_launchctl_requires_explicit_plist_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                env_overrides=fake_env,
                check=False,
            )

            self.assertFalse(runtime_dir.exists())
            self.assertEqual(self.read_launchctl_calls(log_path), [])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--plist-path is required", result.stderr)

    def test_launchctl_bootstrap_invokes_fake_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            calls = self.read_launchctl_calls(log_path)

        self.assertEqual(result.stderr, "")
        self.assertIn("launchctl_action: bootstrap", result.stdout)
        self.assertEqual(calls, [["bootstrap", f"gui/{os.getuid()}", str(plist_path.resolve())]])
        self.assertEqual(state["mode"], "launchctl-bootstrap")
        self.assertTrue(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_action"], "bootstrap")
        self.assertEqual(state["launchctl_returncode"], 0)
        self.assertEqual(state["launchctl_status"], "ok")

    def test_launchctl_kickstart_status_and_bootout_command_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            for mode in ("--launchctl-kickstart", "--launchctl-status", "--launchctl-bootout"):
                self.run_daemon(runtime_dir, mode, "--confirm-launchctl", "--plist-path", str(plist_path), env_overrides=fake_env)
            calls = self.read_launchctl_calls(log_path)

        target = f"gui/{os.getuid()}/com.claude-code-compact-sidecar.daemon"
        self.assertEqual(
            calls,
            [
                ["kickstart", "-k", target],
                ["print", target],
                ["bootout", target],
            ],
        )

    def test_launchctl_refuses_missing_plist_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "missing.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
                check=False,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launchctl_status: missing-plist", result.stdout)
        self.assertEqual(calls, [])
        self.assertFalse(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_status"], "missing-plist")

    def test_launchctl_refuses_malformed_plist_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "bad.plist"
            plist_path.write_text("not plist", encoding="utf-8")
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
                check=False,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launchctl_status: invalid-plist", result.stdout)
        self.assertEqual(calls, [])
        self.assertFalse(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_status"], "invalid-plist")

    def test_launchctl_refuses_non_sidecar_plist_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "not-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "not.sidecar"}))
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
                check=False,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launchctl_status: refused", result.stdout)
        self.assertEqual(calls, [])
        self.assertFalse(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_status"], "refused")

    def test_launchctl_missing_executable_records_failure_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            missing_launchctl = temp_path / "missing-launchctl"

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(
                runtime_dir,
                "--launchctl-status",
                "--plist-path",
                str(plist_path),
                env_overrides={"SIDECAR_LAUNCHCTL_PATH": str(missing_launchctl)},
                check=False,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("launchctl_status: failed", result.stdout)
        self.assertTrue(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_status"], "failed")
        self.assertEqual(state["launchctl_returncode"], 1)
        self.assertEqual(state["error_kind"], "FileNotFoundError")

    def test_launchctl_refuses_invalid_sidecar_plist_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "invalid-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "com.claude-code-compact-sidecar.daemon"}))
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(
                runtime_dir,
                "--launchctl-bootstrap",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
                check=False,
            )
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launchctl_status: refused", result.stdout)
        self.assertEqual(calls, [])
        self.assertFalse(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_status"], "refused")

    def test_launchctl_failure_records_metadata_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path, exit_code=42)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(
                runtime_dir,
                "--launchctl-status",
                "--plist-path",
                str(plist_path),
                env_overrides=fake_env,
                check=False,
            )
            state_text = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")
            state = json.loads(state_text)
            calls = self.read_launchctl_calls(log_path)

        self.assertEqual(calls, [["print", f"gui/{os.getuid()}/com.claude-code-compact-sidecar.daemon"]])
        self.assertEqual(result.returncode, 42)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(state["launchctl_invoked"])
        self.assertEqual(state["launchctl_returncode"], 42)
        self.assertEqual(state["launchctl_status"], "failed")
        self.assertNotIn("compact summary", state_text)

    def test_doctor_requires_explicit_plist_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            result = self.run_daemon(runtime_dir, "--doctor", check=False)

            self.assertFalse(runtime_dir.exists())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--plist-path is required", result.stderr)

    def test_doctor_reports_missing_plist_without_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "missing.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(runtime_dir, "--doctor", "--plist-path", str(plist_path), env_overrides=fake_env, check=False)

            self.assertFalse(runtime_dir.exists())
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("Sidecar daemon doctor", result.stdout)
        self.assertIn("plist: absent", result.stdout)
        self.assertIn("launchctl_registered: unknown", result.stdout)
        self.assertIn("status: absent", result.stdout)
        self.assertEqual(calls, [])

    def test_doctor_reports_malformed_plist_without_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "bad.plist"
            plist_path.write_text("not plist", encoding="utf-8")
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(runtime_dir, "--doctor", "--plist-path", str(plist_path), env_overrides=fake_env, check=False)

            self.assertFalse(runtime_dir.exists())
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("plist: present", result.stdout)
        self.assertIn("plist_valid: no", result.stdout)
        self.assertIn("status: invalid", result.stdout)
        self.assertEqual(calls, [])

    def test_doctor_reports_invalid_sidecar_plist_without_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "invalid-sidecar.plist"
            plist_path.write_bytes(plistlib.dumps({"Label": "com.claude-code-compact-sidecar.daemon"}))
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            result = self.run_daemon(runtime_dir, "--doctor", "--plist-path", str(plist_path), env_overrides=fake_env, check=False)

            self.assertFalse(runtime_dir.exists())
            calls = self.read_launchctl_calls(log_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("label_match=yes", result.stdout)
        self.assertIn("plist_valid: no", result.stdout)
        self.assertIn("status: invalid", result.stdout)
        self.assertEqual(calls, [])

    def test_doctor_reports_registered_service_with_fake_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            state_before = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")
            result = self.run_daemon(runtime_dir, "--doctor", "--plist-path", str(plist_path), env_overrides=fake_env)
            state_after = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")
            calls = self.read_launchctl_calls(log_path)

        target = f"gui/{os.getuid()}/com.claude-code-compact-sidecar.daemon"
        self.assertEqual(result.stderr, "")
        self.assertIn("plist_valid: yes", result.stdout)
        self.assertIn("launchctl_registered: yes", result.stdout)
        self.assertIn("status: ok", result.stdout)
        self.assertEqual(calls, [["print", target]])
        self.assertEqual(state_after, state_before)

    def test_doctor_reports_unregistered_service_with_fake_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path, exit_code=113)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(runtime_dir, "--doctor", "--plist-path", str(plist_path), env_overrides=fake_env, check=False)
            calls = self.read_launchctl_calls(log_path)

        target = f"gui/{os.getuid()}/com.claude-code-compact-sidecar.daemon"
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("launchctl_returncode: 113", result.stdout)
        self.assertIn("launchctl_registered: no", result.stdout)
        self.assertIn("status: not-registered", result.stdout)
        self.assertEqual(calls, [["print", target]])

    def test_agent_status_does_not_invoke_fake_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, fake_env = self.make_fake_launchctl(temp_path)

            self.run_daemon(runtime_dir, "--install-agent", "--plist-path", str(plist_path))
            result = self.run_daemon(runtime_dir, "--agent-status", "--plist-path", str(plist_path), env_overrides=fake_env)
            calls = self.read_launchctl_calls(log_path)

        self.assertEqual(result.returncode, 0)
        self.assertIn("Sidecar daemon agent-status", result.stdout)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
