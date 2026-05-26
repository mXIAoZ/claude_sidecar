from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "auto_compact_controller.py"


class AutoCompactControllerTests(unittest.TestCase):
    def run_controller(
        self,
        runtime_dir: Path,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=stdin,
            check=check,
            text=True,
            capture_output=True,
            env=env,
        )

    def make_fake_tmux(self, temp_path: Path, *, exit_code: int = 0, append_history: Path | None = None) -> tuple[Path, Path]:
        log_path = temp_path / "tmux-calls.jsonl"
        script_path = temp_path / "fake-tmux.py"
        lines = [
            f"#!{sys.executable}",
            "import json",
            "import os",
            "import sys",
            "args = sys.argv[1:]",
            "with open(os.environ['FAKE_TMUX_LOG'], 'a', encoding='utf-8') as handle:",
            "    handle.write(json.dumps(args, ensure_ascii=False) + '\\n')",
        ]
        if append_history is not None:
            lines.extend(
                [
                    "if '/compact' in args:",
                    "    record = {'timestamp': '2026-05-21T12:00:00+00:00', 'payload': {'summary': 'fake compact summary'}}",
                    "    with open(os.environ['FAKE_TMUX_HISTORY'], 'a', encoding='utf-8') as handle:",
                    "        handle.write(json.dumps(record, ensure_ascii=False) + '\\n')",
                ]
            )
        lines.append("raise SystemExit(int(os.environ.get('FAKE_TMUX_EXIT', '0')))" )
        script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        script_path.chmod(0o755)
        os.environ["FAKE_TMUX_LOG"] = str(log_path)
        os.environ["FAKE_TMUX_EXIT"] = str(exit_code)
        if append_history is not None:
            os.environ["FAKE_TMUX_HISTORY"] = str(append_history)
        return script_path, log_path

    def read_tmux_calls(self, log_path: Path) -> list[list[str]]:
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    def read_operation_records(self, runtime_dir: Path) -> list[dict]:
        path = runtime_dir / "operation-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def write_history_record(self, path: Path, summary: str, timestamp: str = "2026-05-21T10:00:00+00:00") -> None:
        record = {"timestamp": timestamp, "payload": {"summary": summary}}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_dry_run_with_missing_runtime_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "missing"
            result = self.run_controller(runtime_dir, "--pane", "session:1.0")

            self.assertFalse(runtime_dir.exists())

        self.assertEqual(result.stderr, "")
        self.assertIn("mode: dry-run", result.stdout)
        self.assertIn("actions: noop", result.stdout)

    def test_missing_pane_fails_before_tmux_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--prompt-file",
                str(prompt_path),
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
                check=False,
            )

            self.assertEqual(self.read_tmux_calls(log_path), [])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--pane is required", result.stderr)

    def test_no_dry_run_requires_confirm_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            tmux_path, log_path = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--no-dry-run",
                "--tmux-path",
                str(tmux_path),
                check=False,
            )

            self.assertEqual(self.read_tmux_calls(log_path), [])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-send is required", result.stderr)

    def test_confirm_send_without_no_dry_run_remains_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            tmux_path, log_path = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
            )

            self.assertEqual(self.read_tmux_calls(log_path), [])

        self.assertIn("mode: dry-run", result.stdout)

    def test_low_readiness_sends_only_prompt(self) -> None:
        prompt_text = "send this prompt"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(prompt_text, encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
            )
            calls = self.read_tmux_calls(log_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn(prompt_text, calls[0])
        self.assertNotIn("/compact", json.dumps(calls))

    def test_high_readiness_sends_compact_before_prompt(self) -> None:
        prompt_text = "prompt after compact"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.draft.md").write_text("D" * 160_000, encoding="utf-8")
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(prompt_text, encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
            )
            calls = self.read_tmux_calls(log_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0], ["send-keys", "-t", "session:1.0", "-l", "/compact"])
        self.assertEqual(calls[1], ["send-keys", "-t", "session:1.0", "C-m"])
        self.assertIn(prompt_text, calls[2])

    def test_prompt_sources_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text("secret prompt", encoding="utf-8")
            result = self.run_controller(
                runtime_dir,
                "--prompt-file",
                str(prompt_path),
                "--prompt-stdin",
                stdin="stdin prompt",
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)

    def test_prompt_text_is_not_printed_or_written_to_runtime(self) -> None:
        secret = "SUPER_SECRET_PROMPT_TEXT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(secret, encoding="utf-8")
            result = self.run_controller(runtime_dir, "--pane", "session:1.0", "--prompt-file", str(prompt_path))
            runtime_files = [path for path in runtime_dir.iterdir() if path.name != "prompt.txt"]

        self.assertEqual(result.stderr, "")
        self.assertNotIn(secret, result.stdout)
        self.assertEqual(runtime_files, [])

    def test_operation_log_records_metadata_without_raw_prompt_by_default(self) -> None:
        secret = "METADATA_ONLY_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(secret, encoding="utf-8")

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--operation-log",
            )
            records = self.read_operation_records(runtime_dir)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["operation"], "dry-run")
        self.assertEqual(records[0]["metadata"]["prompt_chars"], len(secret))
        self.assertNotIn("raw", records[0])
        self.assertNotIn(secret, json.dumps(records[0], ensure_ascii=False))

    def test_log_raw_prompt_requires_operation_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text("prompt", encoding="utf-8")

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--log-raw-prompt",
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--log-raw-prompt requires --operation-log", result.stderr)

    def test_log_raw_prompt_stores_prompt_when_explicitly_enabled(self) -> None:
        secret = "RAW_CONTROLLER_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(secret, encoding="utf-8")

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--operation-log",
                "--log-raw-prompt",
            )
            records = self.read_operation_records(runtime_dir)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(records[0]["raw"]["prompt"], secret)
        self.assertTrue(records[0]["content_policy"]["raw_prompt_logged"])

    def test_wait_postcompact_detects_history_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.draft.md").write_text("D" * 160_000, encoding="utf-8")
            history_path = runtime_dir / "compact-history.jsonl"
            tmux_path, _ = self.make_fake_tmux(runtime_dir, append_history=history_path)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--wait-postcompact",
                "--wait-timeout-seconds",
                "1",
                "--poll-interval-seconds",
                "0.01",
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("postcompact_changed=yes", result.stdout)

    def test_wait_postcompact_timeout_does_not_create_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.draft.md").write_text("D" * 160_000, encoding="utf-8")
            tmux_path, _ = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--wait-postcompact",
                "--wait-timeout-seconds",
                "0.01",
                "--poll-interval-seconds",
                "0.01",
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
                check=False,
            )

            self.assertFalse((runtime_dir / "compact-history.jsonl").exists())

        self.assertEqual(result.returncode, 3)
        self.assertIn("postcompact_changed=no", result.stdout)

    def test_merge_after_writes_draft_without_overwriting_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            summary_path = runtime_dir / "rolling-summary.md"
            summary_path.write_text("human summary", encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "summary from compact history")
            (runtime_dir / "daemon-state.json").write_text("D" * 160_000, encoding="utf-8")
            tmux_path, _ = self.make_fake_tmux(runtime_dir)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--merge-after",
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
            )
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")
            summary = summary_path.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0)
        self.assertIn("summary from compact history", draft)
        self.assertEqual(summary, "human summary")

    def test_dry_run_merge_after_does_not_write_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", "history summary")
            (runtime_dir / "daemon-state.json").write_text("D" * 160_000, encoding="utf-8")

            self.run_controller(runtime_dir, "--pane", "session:1.0", "--merge-after")

            self.assertFalse((runtime_dir / "rolling-summary.draft.md").exists())

    def test_tmux_failure_does_not_leak_prompt(self) -> None:
        secret = "FAILED_SECRET_PROMPT_TEXT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt_path = runtime_dir / "prompt.txt"
            prompt_path.write_text(secret, encoding="utf-8")
            tmux_path, _ = self.make_fake_tmux(runtime_dir, exit_code=7)

            result = self.run_controller(
                runtime_dir,
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--no-dry-run",
                "--confirm-send",
                "--tmux-path",
                str(tmux_path),
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)
        self.assertIn("tmux prompt send failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
