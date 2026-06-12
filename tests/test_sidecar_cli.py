from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE = "compact_sidecar.cli"


class SidecarCliTests(unittest.TestCase):
    def run_sidecar(
        self,
        runtime_dir: Path,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["SIDECAR_COMPACT_DIR"] = str(runtime_dir)
        if env_overrides is not None:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-m", MODULE, *args],
            input=stdin,
            check=check,
            text=True,
            capture_output=True,
            env=env,
        )

    def make_fake_tmux(self, temp_path: Path) -> tuple[Path, Path]:
        log_path = temp_path / "tmux-calls.jsonl"
        script_path = temp_path / "fake-tmux.py"
        script_path.write_text(
            "\n".join(
                [
                    f"#!{sys.executable}",
                    "import json",
                    "import os",
                    "import sys",
                    "with open(os.environ['FAKE_TMUX_LOG'], 'a', encoding='utf-8') as handle:",
                    "    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')",
                    "raise SystemExit(0)",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        return script_path, log_path

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

    def read_jsonl(self, path: Path) -> list[list[str]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_help_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_sidecar(Path(temp_dir), "--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Unified Claude Code compact sidecar CLI", result.stdout)

    def test_setup_copies_default_config_to_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"

            result = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path))
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            config_path = runtime_dir / "sidecar.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UserPromptSubmit", settings["hooks"])
        self.assertEqual(config["schema_version"], 1)
        self.assertIn("runtime_config: ", result.stdout)

    def test_setup_explicit_settings_writes_hooks_and_plist_without_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"

            result = self.run_sidecar(
                runtime_dir,
                "setup",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
            )
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            plist_exists = plist_path.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(plist_exists)
        self.assertIn("UserPromptSubmit", settings["hooks"])
        self.assertIn("launchctl was not invoked", result.stdout)

    def test_setup_start_daemon_can_skip_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)

            result = self.run_sidecar(
                temp_path / "runtime",
                "setup",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--start-daemon",
                "--no-launchctl",
                env_overrides=env,
            )
            settings_exists = settings_path.exists()
            plist_exists = plist_path.exists()
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(settings_exists)
        self.assertTrue(plist_exists)
        self.assertEqual(calls, [])
        self.assertIn("launchctl_disabled=yes", result.stdout)

    def test_setup_prompt_requires_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            prompt_path = temp_path / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")

            result = self.run_sidecar(
                temp_path / "runtime",
                "setup",
                "--settings",
                str(settings_path),
                "--prompt-file",
                str(prompt_path),
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--pane is required", result.stderr)

    def test_setup_prompt_no_send_does_not_require_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"
            prompt_path = temp_path / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")

            result = self.run_sidecar(
                temp_path / "runtime",
                "setup",
                "--settings",
                str(settings_path),
                "--prompt-file",
                str(prompt_path),
                "--no-send",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("send_disabled=yes", result.stdout)

    def test_setup_pane_next_command_includes_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            settings_path = temp_path / "settings.json"

            result = self.run_sidecar(
                temp_path / "runtime",
                "setup",
                "--settings",
                str(settings_path),
                "--pane",
                "session:1.0",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"PYTHONPATH={PROJECT_ROOT / 'src'}", result.stdout)
        self.assertIn("python3 -m compact_sidecar.cli start compact", result.stdout)

    def test_start_daemon_uses_fake_launchctl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            launchctl_path, log_path, env = self.make_fake_launchctl(temp_path)
            result = self.run_sidecar(
                temp_path / "runtime",
                "start",
                "daemon",
                "--plist-path",
                str(temp_path / "sidecar.plist"),
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)
            launchctl_exists = launchctl_path.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(launchctl_exists)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], "bootstrap")
        self.assertEqual(calls[1][0], "kickstart")
        self.assertEqual(calls[2][0], "print")

    def test_start_daemon_no_launchctl_installs_plist_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)
            result = self.run_sidecar(
                temp_path / "runtime",
                "start",
                "daemon",
                "--plist-path",
                str(plist_path),
                "--no-launchctl",
                env_overrides=env,
            )
            plist_exists = plist_path.exists()
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(plist_exists)
        self.assertEqual(calls, [])
        self.assertIn("launchctl_disabled=yes", result.stdout)

    def test_start_compact_no_send_skips_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            prompt_path = temp_path / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(temp_path)
            result = self.run_sidecar(
                temp_path,
                "start",
                "compact",
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--no-send",
                "--tmux-path",
                str(tmux_path),
                env_overrides={"FAKE_TMUX_LOG": str(log_path)},
            )
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("send_disabled=yes", result.stdout)
        self.assertEqual(calls, [])

    def test_start_compact_sends_prompt_with_fake_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            prompt_path = temp_path / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(temp_path)
            result = self.run_sidecar(
                temp_path,
                "start",
                "compact",
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--tmux-path",
                str(tmux_path),
                env_overrides={"FAKE_TMUX_LOG": str(log_path)},
            )
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sent_prompt=yes", result.stdout)
        self.assertEqual(len(calls), 2)
        self.assertIn("hello", calls[0])

    def test_compact_alias_sends_prompt_with_fake_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            prompt_path = temp_path / "prompt.txt"
            prompt_path.write_text("hello", encoding="utf-8")
            tmux_path, log_path = self.make_fake_tmux(temp_path)
            result = self.run_sidecar(
                temp_path,
                "compact",
                "--pane",
                "session:1.0",
                "--prompt-file",
                str(prompt_path),
                "--tmux-path",
                str(tmux_path),
                env_overrides={"FAKE_TMUX_LOG": str(log_path)},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sent_prompt=yes", result.stdout)

    def test_status_json_hides_raw_content_by_default(self) -> None:
        secret = "RAW_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            record = {
                "schema_version": 1,
                "timestamp": "2026-05-21T10:00:00+00:00",
                "service": "auto-compact-controller",
                "operation": "send-prompt",
                "status": "ok",
                "metadata": {},
                "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                "raw": {"prompt": secret},
            }
            (runtime_dir / "operation-log.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = self.run_sidecar(runtime_dir, "status", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn('"raw"', result.stdout)

    def test_status_accepts_config_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            configured_runtime = temp_path / "configured-runtime"
            configured_runtime.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"paths": {"runtime_dir": str(configured_runtime)}}), encoding="utf-8")

            result = self.run_sidecar(
                temp_path / "unused-runtime",
                "status",
                "--config",
                str(config_path),
                "--json",
                env_overrides={"SIDECAR_COMPACT_DIR": ""},
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["runtime_dir"], str(configured_runtime))

    def test_status_json_show_content_reveals_raw_content(self) -> None:
        secret = "RAW_SECRET_PROMPT"
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            record = {
                "schema_version": 1,
                "timestamp": "2026-05-21T10:00:00+00:00",
                "service": "auto-compact-controller",
                "operation": "send-prompt",
                "status": "ok",
                "metadata": {},
                "content_policy": {"raw_prompt_logged": True, "raw_summary_logged": False},
                "raw": {"prompt": secret},
            }
            (runtime_dir / "operation-log.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = self.run_sidecar(runtime_dir, "status", "--json", "--show-content")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(secret, result.stdout)
        self.assertIn('"raw"', result.stdout)

    def test_hooks_help_keeps_confirmation_compatibility_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_sidecar(Path(temp_dir), "hooks", "--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("--confirm-user-settings", result.stdout)

    def test_status_doctor_requires_plist_path_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_sidecar(Path(temp_dir), "status", "--json", "--doctor", check=False)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--plist-path is required with --doctor", result.stderr)

    def test_status_json_doctor_output_remains_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            result = self.run_sidecar(
                temp_path / "runtime",
                "status",
                "--json",
                "--doctor",
                "--plist-path",
                str(temp_path / "missing.plist"),
                check=False,
            )
            payload = json.loads(result.stdout)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("doctor", payload)
        self.assertIn("exit_code", payload["doctor"])
        self.assertIn("text", payload["doctor"])

    def test_uninstall_removes_hooks_from_explicit_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path))
            result = self.run_sidecar(runtime_dir, "uninstall", "--settings", str(settings_path))
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("hooks", settings)
        self.assertIn("Removed 3 sidecar hooks", result.stdout)

    def test_uninstall_remove_daemon_requires_plist_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_sidecar(Path(temp_dir), "uninstall", "--remove-daemon", check=False)

        self.assertEqual(result.returncode, 2)
        self.assertIn("--plist-path is required with --remove-daemon", result.stderr)

    def test_uninstall_remove_daemon_no_launchctl_removes_plist_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            result = self.run_sidecar(
                runtime_dir,
                "uninstall",
                "--settings",
                str(settings_path),
                "--remove-daemon",
                "--plist-path",
                str(plist_path),
                "--no-launchctl",
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plist_path.exists())
        self.assertNotIn("hooks", settings)
        self.assertEqual(calls, [])
        self.assertIn("launchctl_disabled=yes", result.stdout)
        self.assertIn("plist_removed: yes", result.stdout)

    def test_uninstall_keep_hooks_removes_daemon_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            result = self.run_sidecar(
                runtime_dir,
                "uninstall",
                "--settings",
                str(settings_path),
                "--keep-hooks",
                "--remove-daemon",
                "--plist-path",
                str(plist_path),
                "--no-launchctl",
                env_overrides=env,
            )
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            calls = self.read_jsonl(log_path)
            plist_exists = plist_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plist_exists)
        self.assertIn("UserPromptSubmit", settings["hooks"])
        self.assertEqual(calls, [])

    def test_uninstall_bootout_failure_blocks_remove_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path, exit_code=42)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            result = self.run_sidecar(
                runtime_dir,
                "uninstall",
                "--settings",
                str(settings_path),
                "--remove-daemon",
                "--plist-path",
                str(plist_path),
                env_overrides=env,
                check=False,
            )
            calls = self.read_jsonl(log_path)
            plist_exists = plist_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 42)
        self.assertTrue(plist_exists)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "bootout")
        self.assertNotIn("plist_removed: yes", result.stdout)

    def test_uninstall_ignore_bootout_failure_still_removes_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path, exit_code=42)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            result = self.run_sidecar(
                runtime_dir,
                "uninstall",
                "--settings",
                str(settings_path),
                "--remove-daemon",
                "--plist-path",
                str(plist_path),
                "--ignore-bootout-failure",
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)
            plist_exists = plist_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plist_exists)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "bootout")
        self.assertIn("plist_removed: yes", result.stdout)

    def test_uninstall_remove_daemon_boots_out_before_removing_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            result = self.run_sidecar(
                runtime_dir,
                "uninstall",
                "--settings",
                str(settings_path),
                "--remove-daemon",
                "--plist-path",
                str(plist_path),
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)
            plist_exists = plist_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plist_exists)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "bootout")
        self.assertIn("plist_removed: yes", result.stdout)

    def test_clean_dry_run_reports_targets_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            unknown_path = runtime_dir / "keep.txt"

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            (runtime_dir / "rolling-summary.md").write_text("raw summary must not be printed", encoding="utf-8")
            unknown_path.write_text("keep", encoding="utf-8")
            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
            )
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            plist_exists = plist_path.exists()
            summary_exists = (runtime_dir / "rolling-summary.md").exists()
            unknown_exists = unknown_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(plist_exists)
        self.assertTrue(summary_exists)
        self.assertTrue(unknown_exists)
        self.assertIn("dry_run=yes", result.stdout)
        self.assertIn("rolling-summary.md", result.stdout)
        self.assertNotIn("raw summary must not be printed", result.stdout)
        self.assertIn("UserPromptSubmit", settings["hooks"])

    def test_clean_force_removes_hooks_plist_and_allowlisted_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            unknown_path = runtime_dir / "keep.txt"
            unknown_dir = runtime_dir / "keep-dir"

            _, log_path, env = self.make_fake_launchctl(temp_path)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            for name in (
                "rolling-summary.md",
                "rolling-summary.draft.md",
                "compact-history.jsonl",
                "compact-history.jsonl.1",
                "operation-log.jsonl",
                "operation-log.jsonl.1",
                "errors.log",
                "daemon-state.json",
                "daemon.out.log",
                "daemon.err.log",
                "rolling-summary.backup.20260611.md",
            ):
                (runtime_dir / name).write_text(f"content for {name}", encoding="utf-8")
            unknown_path.write_text("keep", encoding="utf-8")
            unknown_dir.mkdir()
            (unknown_dir / "nested.txt").write_text("keep", encoding="utf-8")

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            remaining = sorted(path.name for path in runtime_dir.iterdir())

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plist_path.exists())
        self.assertNotIn("hooks", settings)
        self.assertEqual(remaining, ["keep-dir", "keep.txt"])
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "bootout")
        self.assertIn("com.claude-code-compact-sidecar.daemon", calls[0][1])
        self.assertIn("dry_run=no", result.stdout)
        self.assertIn("runtime_removed=12", result.stdout)
        self.assertIn("runtime_skipped=2", result.stdout)

    def test_clean_bootout_failure_blocks_destructive_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path, exit_code=42)

            setup = self.run_sidecar(runtime_dir, "setup", "--settings", str(settings_path), "--plist-path", str(plist_path))
            (runtime_dir / "rolling-summary.md").write_text("keep until bootout succeeds", encoding="utf-8")
            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
                check=False,
            )
            calls = self.read_jsonl(log_path)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            summary_exists = (runtime_dir / "rolling-summary.md").exists()
            plist_exists = plist_path.exists()

        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(result.returncode, 42)
        self.assertEqual(calls[0][0], "bootout")
        self.assertTrue(plist_exists)
        self.assertTrue(summary_exists)
        self.assertIn("UserPromptSubmit", settings["hooks"])
        self.assertIn("launchctl_bootout=failed", result.stdout)

    def test_clean_refuses_non_sidecar_plist_before_runtime_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "sidecar.plist"
            _, _, env = self.make_fake_launchctl(temp_path)
            plist_path.write_text("not a plist", encoding="utf-8")
            (runtime_dir / "rolling-summary.md").write_text("keep", encoding="utf-8")

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
                check=False,
            )
            plist_exists = plist_path.exists()
            summary_exists = (runtime_dir / "rolling-summary.md").exists()

        self.assertEqual(result.returncode, 1)
        self.assertTrue(plist_exists)
        self.assertTrue(summary_exists)
        self.assertIn("plist_removed=no", result.stdout)

    def test_clean_force_boots_out_fixed_label_without_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            plist_path = temp_path / "missing.plist"
            _, log_path, env = self.make_fake_launchctl(temp_path)

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--settings",
                str(settings_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "bootout")
        self.assertIn("com.claude-code-compact-sidecar.daemon", calls[0][1])
        self.assertIn("launchctl_bootout=ok", result.stdout)

    def test_clean_defaults_to_project_settings_not_global_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            project_settings = temp_path / ".claude" / "settings.local.json"
            global_settings = temp_path / "home" / ".claude" / "settings.json"
            project_settings.parent.mkdir()
            global_settings.parent.mkdir(parents=True)
            project_settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": []}}), encoding="utf-8")
            global_settings.write_text(json.dumps({"global": True}), encoding="utf-8")
            _, _, env = self.make_fake_launchctl(temp_path)
            env["HOME"] = str(temp_path / "home")

            result = subprocess.run(
                [sys.executable, "-m", MODULE, "clean", "--force", "--runtime-dir", str(runtime_dir)],
                cwd=temp_path,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "SIDECAR_COMPACT_DIR": str(runtime_dir), **env},
            )
            global_payload = json.loads(global_settings.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(global_payload, {"global": True})

    def test_clean_uses_fixed_launchctl_label_even_when_config_overrides_daemon_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"daemon_launchd": {"agent_label": "com.example.not-sidecar"}}), encoding="utf-8")
            _, log_path, env = self.make_fake_launchctl(temp_path)

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--config",
                str(config_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
            )
            calls = self.read_jsonl(log_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], ["bootout", f"gui/{os.getuid()}/com.claude-code-compact-sidecar.daemon"])
        self.assertNotIn("com.example.not-sidecar", result.stdout)

    def test_clean_refuses_configured_non_sidecar_label_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            config_path = temp_path / "sidecar.config.json"
            config_path.write_text(json.dumps({"daemon_launchd": {"agent_label": "com.example.not-sidecar"}}), encoding="utf-8")
            plist_path = temp_path / "other.plist"
            plist = {
                "Label": "com.example.not-sidecar",
                "ProgramArguments": [sys.executable, "-m", "compact_sidecar.services.daemon", "--loop", "--interval-seconds", "300"],
                "WorkingDirectory": str(temp_path),
                "EnvironmentVariables": {"SIDECAR_COMPACT_DIR": str(runtime_dir)},
                "RunAtLoad": False,
                "KeepAlive": False,
            }
            plist_path.write_bytes(plistlib.dumps(plist))
            (runtime_dir / "rolling-summary.md").write_text("keep", encoding="utf-8")
            _, _, env = self.make_fake_launchctl(temp_path)

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--force",
                "--config",
                str(config_path),
                "--plist-path",
                str(plist_path),
                "--runtime-dir",
                str(runtime_dir),
                env_overrides=env,
                check=False,
            )
            plist_exists = plist_path.exists()
            summary_exists = (runtime_dir / "rolling-summary.md").exists()

        self.assertEqual(result.returncode, 1)
        self.assertTrue(plist_exists)
        self.assertTrue(summary_exists)
        self.assertIn("plist_status=refused", result.stdout)

    def test_clean_json_dry_run_hides_runtime_file_contents(self) -> None:
        secret = "RAW_SECRET_SUMMARY_CONTENT"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            runtime_dir.mkdir()
            (runtime_dir / "rolling-summary.md").write_text(secret, encoding="utf-8")
            (runtime_dir / "operation-log.jsonl").write_text(secret, encoding="utf-8")
            (runtime_dir / "errors.log").write_text(secret, encoding="utf-8")

            result = self.run_sidecar(runtime_dir, "clean", "--runtime-dir", str(runtime_dir), "--json")
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rolling-summary.md", payload["runtime_remove"])
        self.assertNotIn(secret, result.stdout)

    def test_clean_defaults_to_project_root_settings_from_nested_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / ".git").mkdir()
            nested = temp_path / "nested" / "child"
            nested.mkdir(parents=True)
            runtime_dir = temp_path / "runtime"
            project_settings = temp_path / ".claude" / "settings.local.json"
            project_settings.parent.mkdir()
            project_settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": []}}), encoding="utf-8")
            _, _, env = self.make_fake_launchctl(temp_path)

            result = subprocess.run(
                [sys.executable, "-m", MODULE, "clean", "--force", "--runtime-dir", str(runtime_dir)],
                cwd=nested,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "SIDECAR_COMPACT_DIR": str(runtime_dir), **env},
            )
            project_payload = json.loads(project_settings.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("hooks", project_payload)

    def test_clean_malformed_settings_reports_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            settings_path = temp_path / "settings.json"
            settings_path.write_text("not json", encoding="utf-8")

            result = self.run_sidecar(
                runtime_dir,
                "clean",
                "--settings",
                str(settings_path),
                "--runtime-dir",
                str(runtime_dir),
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("error=", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
