from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def packaged_py_modules() -> list[str]:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^py-modules\s*=\s*\[(.*?)\]", pyproject, re.MULTILINE)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


class PackagedEntrypointTests(unittest.TestCase):
    def test_compact_sidecar_cli_imports_with_declared_packaged_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir) / "site-packages"
            shutil.copytree(PROJECT_ROOT / "src" / "compact_sidecar", site_packages / "compact_sidecar")
            for module in packaged_py_modules():
                shutil.copy2(PROJECT_ROOT / "src" / f"{module}.py", site_packages / f"{module}.py")
            shutil.copy2(PROJECT_ROOT / "sidecar.config.template.json", site_packages / "sidecar.config.template.json")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(site_packages)
            result = subprocess.run(
                [sys.executable, "-c", "from compact_sidecar.cli import main; print(callable(main))"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()
