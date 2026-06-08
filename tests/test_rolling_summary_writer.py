from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from compact_sidecar.runtime.rolling_summary_writer import (  # noqa: E402
    RollingSummaryError,
    validate_rolling_summary_text,
    write_rolling_summary_with_backup,
)


VALID_SUMMARY = "# Rolling Summary\n\n## Compact 前必须保留\nkeep this\n"


class RollingSummaryWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_validate_accepts_required_header_and_marker(self) -> None:
        self.assertEqual(validate_rolling_summary_text(VALID_SUMMARY), VALID_SUMMARY)

    def test_validate_rejects_missing_marker(self) -> None:
        with self.assertRaises(RollingSummaryError) as raised:
            validate_rolling_summary_text("# Rolling Summary\n\nno marker\n")

        self.assertIn("Compact 前必须保留", str(raised.exception))

    def test_validate_rejects_missing_heading(self) -> None:
        with self.assertRaises(RollingSummaryError) as raised:
            validate_rolling_summary_text("## Compact 前必须保留\nkeep\n")

        self.assertIn("# Rolling Summary", str(raised.exception))

    def test_validate_rejects_prompt_quoted_before_heading(self) -> None:
        with self.assertRaises(RollingSummaryError) as raised:
            validate_rolling_summary_text("Please output # Rolling Summary\n\n# Rolling Summary\n\n## Compact 前必须保留\nkeep\n")

        self.assertIn("start with # Rolling Summary", str(raised.exception))

    def test_validate_rejects_marker_inside_sentence(self) -> None:
        with self.assertRaises(RollingSummaryError) as raised:
            validate_rolling_summary_text("# Rolling Summary\n\nPlease include ## Compact 前必须保留 here\n")

        self.assertIn("standalone", str(raised.exception))

    def test_write_new_summary_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)

            summary_path, backup_path = write_rolling_summary_with_backup(VALID_SUMMARY)
            written = summary_path.read_text(encoding="utf-8")

        self.assertEqual(written, VALID_SUMMARY)
        self.assertEqual(summary_path.name, "rolling-summary.md")
        self.assertIsNone(backup_path)

    def test_write_existing_summary_creates_backup_before_replace(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold\n"
        new_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnew\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
            summary_file = runtime_dir / "rolling-summary.md"
            summary_file.write_text(old_summary, encoding="utf-8")

            summary_path, backup_path = write_rolling_summary_with_backup(new_summary)
            written = summary_path.read_text(encoding="utf-8")
            backup_text = backup_path.read_text(encoding="utf-8") if backup_path is not None else ""
            backups = list(runtime_dir.glob("rolling-summary.backup.*.md"))

        self.assertEqual(written, new_summary)
        self.assertEqual(backup_text, old_summary)
        self.assertIsNotNone(backup_path)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].name, backup_path.name)

    def test_failed_replace_keeps_existing_summary(self) -> None:
        old_summary = "# Rolling Summary\n\n## Compact 前必须保留\nold\n"
        new_summary = "# Rolling Summary\n\n## Compact 前必须保留\nnew\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            os.environ["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
            summary_file = runtime_dir / "rolling-summary.md"
            summary_file.write_text(old_summary, encoding="utf-8")
            original_replace = Path.replace

            def fail_target_replace(self: Path, target: Path) -> Path:
                if self.name.startswith(".rolling-summary.md.") and target.name == "rolling-summary.md":
                    raise OSError("simulated replace failure")
                return original_replace(self, target)

            with patch.object(Path, "replace", fail_target_replace):
                with self.assertRaises(RollingSummaryError):
                    write_rolling_summary_with_backup(new_summary)
            written = summary_file.read_text(encoding="utf-8")
            backups = list(runtime_dir.glob("rolling-summary.backup.*.md"))
            backup_text = backups[0].read_text(encoding="utf-8") if backups else ""
            temp_files = list(runtime_dir.glob(".rolling-summary.md.*.tmp"))

        self.assertEqual(written, old_summary)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_text, old_summary)
        self.assertEqual(temp_files, [])


if __name__ == "__main__":
    unittest.main()
