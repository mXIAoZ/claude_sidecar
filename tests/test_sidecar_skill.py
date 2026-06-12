from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PROJECT_ROOT / "sidecar-manager-skill" / "SKILL.md"


def read_skill() -> tuple[dict[str, str], str]:
    text = SKILL_PATH.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    metadata: dict[str, str] = {}
    current_key: str | None = None
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current_key == "description":
            metadata["description"] = f"{metadata['description']} {line.strip()}".strip()
            continue
        match = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if match:
            current_key = match.group(1)
            metadata[current_key] = match.group(2).strip().strip('"')
    return metadata, parts[2]


class SidecarSkillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata, self.body = read_skill()
        self.full_text = SKILL_PATH.read_text(encoding="utf-8")

    def test_frontmatter_has_trigger_metadata(self) -> None:
        self.assertEqual(self.metadata["name"], "sidecar-manager")
        self.assertRegex(self.metadata["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        description = self.metadata["description"]
        self.assertGreaterEqual(len(description), 120)
        for phrase in (
            "install hooks",
            "status/health/readiness",
            "LLM environment",
            "daemon plist",
            "uninstall/remove hooks",
        ):
            self.assertIn(phrase, description)
        self.assertIn("version", self.full_text)

    def test_body_stays_focused_and_under_loading_budget(self) -> None:
        line_count = len(self.full_text.splitlines())
        self.assertLessEqual(line_count, 500)
        self.assertIn("## Capability Menu", self.body)
        self.assertIn("## Routing Table", self.body)
        self.assertIn("## Skill Quality Contract", self.body)
        self.assertNotIn("## Option 13", self.body)

    def test_four_workflows_are_present_without_extra_capabilities(self) -> None:
        for heading in ("## 1. Install", "## 2. Monitor", "## 3. Configure", "## 4. Uninstall"):
            self.assertIn(heading, self.body)
        for excluded in (
            "repository indexing",
            "raw prompt audit",
            "daemon internals",
            "architecture explanation",
        ):
            self.assertIn(excluded, self.body)
        self.assertIn("outside this skill's scope", self.body)

    def test_examples_and_checkpoints_cover_operator_paths(self) -> None:
        self.assertIn("## Operator Examples", self.body)
        for example in (
            "Preview install without touching my config",
            "Install hooks for this project",
            "Is the sidecar working?",
            "Check my LLM summary config",
            "Uninstall but don't touch launchctl",
            "Log this prompt and send it to tmux pane X",
        ):
            self.assertIn(example, self.body)
        for checkpoint in ("Install checkpoints", "Monitor checkpoints", "Configure checkpoints", "Uninstall checkpoints"):
            self.assertIn(checkpoint, self.body)
        self.assertIn("setup --settings", self.body)
        self.assertIn("--send --operation-log --log-raw-prompt", self.body)

    def test_safety_contract_mentions_confirmed_risky_operations(self) -> None:
        for phrase in (
            "global settings",
            "explicitly asks",
            "temporary settings",
            "Treat user-provided paths",
            "quoted, separate command arguments",
            "Do not print API key values",
            "Do not add `--show-content`",
            "Do not remove `--no-send`",
            "If a checkpoint fails, stop",
        ):
            self.assertIn(phrase, self.body)
        self.assertIn("--no-launchctl", self.body)
        self.assertIn("--no-operation-log", self.body)

    def test_commands_use_installed_skill_pythonpath(self) -> None:
        self.assertIn('PYTHONPATH="$HOME/.claude/skills/sidecar-manager/src"', self.body)
        self.assertNotIn("PYTHONPATH=src", self.body)

    def test_commands_reuse_existing_entrypoints(self) -> None:
        for module in (
            "compact_sidecar.cli setup",
            "compact_sidecar.cli status --json",
            "compact_sidecar.cli compact",
            "compact_sidecar.cli uninstall",
            "compact_sidecar.services.daemon --launchctl-bootout",
        ):
            self.assertIn(module, self.body)
        self.assertNotIn("rm -rf", self.body)
        self.assertNotIn("curl -H \"Authorization: Bearer", self.body)


if __name__ == "__main__":
    unittest.main()
