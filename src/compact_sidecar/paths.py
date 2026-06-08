from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from compact_sidecar.config import load_config_for_import, load_config_safe

_CONFIG = load_config_for_import()
ENV_RUNTIME_DIR = "SIDECAR_COMPACT_DIR"
DEFAULT_RUNTIME_DIR_NAME = str(_CONFIG["paths"]["default_runtime_dir_name"])
PROJECT_ROOT_MARKERS = tuple(str(marker) for marker in _CONFIG["paths"]["project_root_markers"])
ERRORS_NAME = str(_CONFIG["paths"]["runtime_files"]["errors"])


def project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return candidate
    return current


def runtime_dir() -> Path:
    configured = os.environ.get(ENV_RUNTIME_DIR)
    if configured:
        return Path(configured).expanduser()
    config_runtime = str(load_config_safe()["paths"].get("runtime_dir") or "")
    if config_runtime:
        return Path(config_runtime).expanduser()
    return project_root() / DEFAULT_RUNTIME_DIR_NAME


def runtime_path(name: str) -> Path:
    path = Path(name)
    if not name or name in {".", ".."} or path.is_absolute() or path.name != name or "/" in name or "\\" in name:
        raise ValueError(f"runtime path name must be a file name: {name}")
    return runtime_dir() / name


def write_error(message: str, *, exc: BaseException | None = None, service: str = "sidecar") -> None:
    try:
        directory = runtime_dir()
        directory.mkdir(parents=True, exist_ok=True)
        detail = message if exc is None else f"{message}: {type(exc).__name__}: {exc}"
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "message": detail,
        }
        with (directory / ERRORS_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def noop_response(hook_event_name: str | None = None) -> dict[str, Any]:
    if hook_event_name is None:
        return {}
    return {"hookSpecificOutput": {"hookEventName": hook_event_name}}
