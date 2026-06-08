from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE = "compact_sidecar.hooks.userprompt"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.runtime.readiness import COMPACT_ADVISORY_TITLE, READINESS_HIGH_CHARS
from compact_sidecar.hooks.userprompt import read_stdin_capped


class UserPromptInjectTests(unittest.TestCase):
    def run_script(
        self,
        runtime_dir: Path,
        *,
        inject_always: bool = False,
        stdin: str | None = None,
    ) -> dict:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        if inject_always:
            env["SIDECAR_INJECT_ALWAYS"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", MODULE],
            input=stdin,
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_invalid_explicit_env_config_outputs_noop_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            (runtime_dir / "rolling-summary.md").write_text("## Compact 前必须保留\nkeep", encoding="utf-8")
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"unknown": "value"}}), encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
            env["SIDECAR_CONFIG_PATH"] = str(config_path)

            result = subprocess.run(
                [sys.executable, "-m", MODULE],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )

        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {})

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

    def test_small_prompt_does_not_add_compact_advisory(self) -> None:
        marker = "SIDE_CAR_TEST_MARKER_12345"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text(
                f"## Compact 前必须保留\n{marker}", encoding="utf-8"
            )
            payload = self.run_script(runtime_dir, stdin=json.dumps({"prompt": "short prompt"}))

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(marker, context)
        self.assertNotIn(COMPACT_ADVISORY_TITLE, context)

    def test_large_prompt_outputs_compact_advisory_without_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt = "P" * READINESS_HIGH_CHARS
            payload = self.run_script(runtime_dir, stdin=json.dumps({"prompt": prompt}))
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(COMPACT_ADVISORY_TITLE, context)
        self.assertIn("Consider running /compact", context)
        self.assertNotIn(prompt, context)

    def test_large_prompt_outputs_advisory_with_summary(self) -> None:
        marker = "SIDE_CAR_TEST_MARKER_12345"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text(
                f"## Compact 前必须保留\n{marker}", encoding="utf-8"
            )
            prompt = "P" * READINESS_HIGH_CHARS
            payload = self.run_script(runtime_dir, stdin=json.dumps({"userPrompt": prompt}))

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(COMPACT_ADVISORY_TITLE, context)
        self.assertIn(marker, context)
        self.assertLess(context.find(COMPACT_ADVISORY_TITLE), context.find(marker))
        self.assertNotIn(prompt, context)

    def test_malformed_stdin_preserves_noop_without_logging_prompt_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            payload = self.run_script(runtime_dir, stdin="{")
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        self.assertEqual(payload, {})

    def test_large_malformed_stdin_is_capped_and_non_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            payload = self.run_script(runtime_dir, stdin="{" + ("P" * (READINESS_HIGH_CHARS * 2)))
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(COMPACT_ADVISORY_TITLE, context)
        self.assertNotIn("P" * 100, context)

    def test_capped_large_prompt_payload_still_outputs_compact_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            prompt = "P" * (READINESS_HIGH_CHARS * 2)
            payload = self.run_script(runtime_dir, stdin=json.dumps({"prompt": prompt}))
            errors_path = runtime_dir / "errors.log"

            self.assertFalse(errors_path.exists())

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(COMPACT_ADVISORY_TITLE, context)
        self.assertIn("Estimated local pressure: 200000 chars", context)
        self.assertNotIn(prompt[:100], context)

    def test_summary_is_not_double_counted_in_compact_advisory_estimate(self) -> None:
        marker = "SIDE_CAR_TEST_MARKER_12345"
        summary_filler = "S" * (READINESS_HIGH_CHARS - 1_000)
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            (runtime_dir / "rolling-summary.md").write_text(
                f"## Compact 前必须保留\n{marker}\n{summary_filler}",
                encoding="utf-8",
            )
            payload = self.run_script(runtime_dir, stdin=json.dumps({"prompt": "short prompt"}))

        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn(COMPACT_ADVISORY_TITLE, context)
        self.assertIn(marker, context)

    def test_tty_stdin_is_not_read(self) -> None:
        fake_stdin = Mock()
        fake_stdin.isatty.return_value = True
        with patch("compact_sidecar.hooks.userprompt.sys.stdin", fake_stdin):
            raw, capped = read_stdin_capped()

        self.assertEqual(raw, "")
        self.assertFalse(capped)
        fake_stdin.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
