from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "src" / "userprompt_inject.py"


class UserPromptInjectTests(unittest.TestCase):
    def run_script(self, runtime_dir: Path, *, inject_always: bool = False) -> dict:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        if inject_always:
            env["SIDECAR_INJECT_ALWAYS"] = "1"
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_missing_summary_outputs_noop_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self.run_script(Path(temp_dir))

        self.assertEqual(payload, {})

    def test_empty_summary_outputs_noop_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("\n\t ", encoding="utf-8")
            payload = self.run_script(runtime_dir)

        self.assertEqual(payload, {})

    def test_summary_without_marker_outputs_noop_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("## 当前目标\n验证 sidecar", encoding="utf-8")
            payload = self.run_script(runtime_dir)

        self.assertEqual(payload, {})

    def test_non_empty_summary_is_injected_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text("## 当前目标\n验证 sidecar", encoding="utf-8")
            payload = self.run_script(runtime_dir, inject_always=True)

        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
        self.assertIn("## 当前目标", hook_output["additionalContext"])
        self.assertIn("验证 sidecar", hook_output["additionalContext"])

    def test_marker_summary_is_injected_for_manual_validation(self) -> None:
        marker = "SIDE_CAR_TEST_MARKER_12345"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text(
                f"## Compact 前必须保留\n{marker}", encoding="utf-8"
            )
            payload = self.run_script(runtime_dir)

        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
        self.assertIn(marker, hook_output["additionalContext"])

    def test_oversized_summary_keeps_head_and_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            summary = "## Compact 前必须保留\nHEAD" + ("A" * 45_000) + "TAIL"
            (runtime_dir / "rolling-summary.md").write_text(summary, encoding="utf-8")
            payload = self.run_script(runtime_dir)

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("HEAD", context)
        self.assertIn("TAIL", context)
        self.assertIn("middle was truncated", context)
        self.assertLess(len(context), len(summary))


if __name__ == "__main__":
    unittest.main()
