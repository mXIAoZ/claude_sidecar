from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SidecarPathsTests(unittest.TestCase):
    def run_runtime_dir(self, cwd: Path, env: dict[str, str]) -> str:
        script = (
            f"import sys; sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r}); "
            "from compact_sidecar.paths import runtime_dir; print(runtime_dir())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.stderr, "")
        return result.stdout.strip()

    def test_default_runtime_dir_is_project_memory(self) -> None:
        with tempfile.TemporaryDirectory():
            env = os.environ.copy()
            env.pop("SIDECAR_COMPACT_DIR", None)
            runtime_dir = self.run_runtime_dir(PROJECT_ROOT, env)

        self.assertEqual(runtime_dir, str(PROJECT_ROOT / ".memory"))

    def test_default_runtime_dir_is_project_memory_from_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory():
            env = os.environ.copy()
            env.pop("SIDECAR_COMPACT_DIR", None)
            runtime_dir = self.run_runtime_dir(PROJECT_ROOT / "src", env)

        self.assertEqual(runtime_dir, str(PROJECT_ROOT / ".memory"))

    def test_sidecar_compact_dir_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            override_dir = Path(temp_dir) / "runtime"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            env["SIDECAR_COMPACT_DIR"] = str(override_dir)
            runtime_dir = self.run_runtime_dir(PROJECT_ROOT, env)

        self.assertEqual(runtime_dir, str(override_dir))


if __name__ == "__main__":
    unittest.main()
