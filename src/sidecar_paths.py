from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENV_RUNTIME_DIR = "SIDECAR_COMPACT_DIR"
DEFAULT_RUNTIME_DIR_NAME = ".memory"
PROJECT_ROOT_MARKERS = (".git",)


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
    return project_root() / DEFAULT_RUNTIME_DIR_NAME


def runtime_path(name: str) -> Path:
    return runtime_dir() / name


def write_error(message: str, *, exc: BaseException | None = None) -> None:
    try:
        directory = runtime_dir()
        directory.mkdir(parents=True, exist_ok=True)
        detail = message if exc is None else f"{message}: {type(exc).__name__}: {exc}"
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": detail,
        }
        with (directory / "errors.log").open("a", encoding="utf-8") as handle:
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
