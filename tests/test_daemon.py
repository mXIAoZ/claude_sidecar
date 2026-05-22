from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "daemon.py"


class DaemonRunOnceTests(unittest.TestCase):
    def run_daemon(self, runtime_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=check,
            text=True,
            capture_output=True,
            env=env,
        )

    def write_history_record(self, path: Path, summary: str) -> None:
        record = {
            "timestamp": "2026-05-21T10:00:00+00:00",
            "payload": {"summary": summary},
        }
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_run_once_writes_draft_and_metadata_from_history(self) -> None:
        compact_summary = "daemon compact summary from src/daemon.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            result = self.run_daemon(runtime_dir, "--run-once")
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

    def test_run_once_with_no_history_writes_empty_draft_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            result = self.run_daemon(runtime_dir, "--run-once")
            draft = (runtime_dir / "rolling-summary.draft.md").read_text(encoding="utf-8")
            state = json.loads((runtime_dir / "daemon-state.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stderr, "")
        self.assertIn("No compact history summaries found.", draft)
        self.assertEqual(state["candidate_count"], 0)

    def test_run_once_does_not_overwrite_rolling_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            rolling_summary_path = runtime_dir / "rolling-summary.md"
            rolling_summary_path.write_text("keep this summary", encoding="utf-8")
            self.write_history_record(runtime_dir / "compact-history.jsonl", "new compact summary")

            self.run_daemon(runtime_dir, "--run-once")
            rolling_summary = rolling_summary_path.read_text(encoding="utf-8")

        self.assertEqual(rolling_summary, "keep this summary")

    def test_daemon_state_does_not_store_raw_summary_text(self) -> None:
        compact_summary = "do not persist this raw compact body"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            self.write_history_record(runtime_dir / "compact-history.jsonl", compact_summary)

            self.run_daemon(runtime_dir, "--run-once")
            state_text = (runtime_dir / "daemon-state.json").read_text(encoding="utf-8")

        self.assertNotIn(compact_summary, state_text)

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


if __name__ == "__main__":
    unittest.main()
