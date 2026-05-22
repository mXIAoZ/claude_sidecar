from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USERPROMPT_SCRIPT = PROJECT_ROOT / "src" / "userprompt_inject.py"
POSTCOMPACT_SCRIPT = PROJECT_ROOT / "src" / "postcompact_record.py"
MERGE_SCRIPT = PROJECT_ROOT / "src" / "merge_compact_history.py"


class ManualSmokeFlowTests(unittest.TestCase):
    def run_script(self, script: Path, runtime_dir: Path, *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        return subprocess.run(
            [sys.executable, str(script)],
            input=stdin,
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_manual_smoke_flow_injects_records_and_generates_review_draft(self) -> None:
        marker = "SIDE_CAR_TEST_MARKER_UNITED_FLOW"
        rolling_summary = f"""# Rolling Summary

## 当前目标
验证 sidecar compact smoke flow: {marker}

## Compact 前必须保留
如果 compact 后还能看到 {marker}，说明注入成功。
"""
        postcompact_payload = {
            "session_id": "manual-smoke-test",
            "summary": "Compacted flow kept src/userprompt_inject.py and tests/test_manual_smoke_flow.py in view.",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            rolling_summary_path = runtime_dir / "rolling-summary.md"
            rolling_summary_path.write_text(rolling_summary, encoding="utf-8")

            inject_result = self.run_script(USERPROMPT_SCRIPT, runtime_dir)
            inject_output = json.loads(inject_result.stdout)
            hook_output = inject_output["hookSpecificOutput"]
            additional_context = hook_output["additionalContext"]

            self.assertEqual(inject_result.stderr, "")
            self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
            self.assertIn("Sidecar rolling summary for continuity preservation:", additional_context)
            self.assertIn(marker, additional_context)

            postcompact_result = self.run_script(
                POSTCOMPACT_SCRIPT,
                runtime_dir,
                stdin=json.dumps(postcompact_payload),
            )
            history_path = runtime_dir / "compact-history.jsonl"
            history_records = history_path.read_text(encoding="utf-8").splitlines()
            history_record = json.loads(history_records[0])

            self.assertEqual(postcompact_result.stdout, "")
            self.assertEqual(postcompact_result.stderr, "")
            self.assertEqual(len(history_records), 1)
            self.assertEqual(history_record["payload"], postcompact_payload)
            self.assertIn("timestamp", history_record)
            self.assertIn("payload_bytes", history_record)

            before_merge_summary = rolling_summary_path.read_text(encoding="utf-8")
            merge_result = self.run_script(MERGE_SCRIPT, runtime_dir)
            draft_path = runtime_dir / "rolling-summary.draft.md"
            draft = draft_path.read_text(encoding="utf-8")
            after_merge_summary = rolling_summary_path.read_text(encoding="utf-8")

            self.assertEqual(merge_result.stderr, "")
            self.assertIn("rolling-summary.draft.md", merge_result.stdout)
            self.assertIn("# Rolling Summary Draft", draft)
            self.assertIn(postcompact_payload["summary"], draft)
            self.assertIn("Review hints from compact summary text only:", draft)
            self.assertIn("- `src/userprompt_inject.py`", draft)
            self.assertIn("- `tests/test_manual_smoke_flow.py`", draft)
            self.assertEqual(after_merge_summary, before_merge_summary)
            self.assertFalse((runtime_dir / "settings.json").exists())
            self.assertFalse((runtime_dir / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
