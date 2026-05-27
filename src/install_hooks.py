from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PYTHON = "python3"
USERPROMPT_SCRIPT = "userprompt_inject.py"
POSTCOMPACT_SCRIPT = "postcompact_record.py"


class SettingsError(Exception):
    pass


def script_command(script_name: str) -> str:
    script_path = Path(__file__).resolve().parent / script_name
    return f"{PYTHON} {shlex.quote(str(script_path))}"


def required_hook_specs() -> list[dict[str, str]]:
    return [
        {
            "event": "UserPromptSubmit",
            "matcher": "",
            "script": USERPROMPT_SCRIPT,
            "command": script_command(USERPROMPT_SCRIPT),
            "statusMessage": "Injecting sidecar rolling summary...",
        },
        {
            "event": "PostCompact",
            "matcher": "auto",
            "script": POSTCOMPACT_SCRIPT,
            "command": script_command(POSTCOMPACT_SCRIPT),
            "statusMessage": "Recording compact summary auto...",
        },
        {
            "event": "PostCompact",
            "matcher": "manual",
            "script": POSTCOMPACT_SCRIPT,
            "command": script_command(POSTCOMPACT_SCRIPT),
            "statusMessage": "Recording compact summary manual...",
        },
    ]


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        settings_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"failed to read settings JSON: {exc}") from exc

    try:
        settings = json.loads(settings_text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"failed to parse settings JSON: {exc}") from exc

    if not isinstance(settings, dict):
        raise SettingsError("settings root must be a JSON object")
    return settings


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SettingsError(f"{label} must be a list")
    return value


def command_references_script(command: Any, script_name: str) -> bool:
    if not isinstance(command, str):
        return False

    try:
        parts = shlex.split(command)
    except ValueError:
        return False

    return any(Path(part).name == script_name for part in parts)


def matcher_entry_for(event_hooks: list[Any], matcher: str) -> dict[str, Any]:
    for entry in event_hooks:
        if not isinstance(entry, dict):
            raise SettingsError("hook event entries must be JSON objects")
        if entry.get("matcher", "") == matcher:
            hooks = entry.setdefault("hooks", [])
            require_list(hooks, "hook entry hooks")
            return entry

    entry = {"matcher": matcher, "hooks": []}
    event_hooks.append(entry)
    return entry


def has_sidecar_hook(hooks: list[Any], script_name: str) -> bool:
    for hook in hooks:
        if not isinstance(hook, dict):
            raise SettingsError("hook commands must be JSON objects")
        if hook.get("type") == "command" and command_references_script(hook.get("command"), script_name):
            return True
    return False


def merge_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError("settings.hooks must be a JSON object")

    for spec in required_hook_specs():
        event_hooks = hooks.setdefault(spec["event"], [])
        require_list(event_hooks, f"settings.hooks.{spec['event']}")
        entry = matcher_entry_for(event_hooks, spec["matcher"])
        entry_hooks = require_list(entry.setdefault("hooks", []), "hook entry hooks")

        if has_sidecar_hook(entry_hooks, spec["script"]):
            continue

        entry_hooks.append(
            {
                "type": "command",
                "command": spec["command"],
                "timeout": 5,
                "statusMessage": spec["statusMessage"],
            }
        )

    return settings


def dump_settings(settings: dict[str, Any]) -> str:
    return json.dumps(settings, ensure_ascii=False, indent=2) + "\n"


def write_settings(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise SettingsError(f"failed to write settings JSON: {exc}") from exc


def install(settings_path: Path) -> int:
    try:
        settings = merge_hooks(load_settings(settings_path))
    except SettingsError as exc:
        print(f"install_hooks.py: {exc}", file=sys.stderr)
        return 1

    output = dump_settings(settings)
    try:
        write_settings(settings_path, output)
    except SettingsError as exc:
        print(f"install_hooks.py: {exc}", file=sys.stderr)
        return 1

    print(f"Installed sidecar hooks into {settings_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Claude Code sidecar compact hooks.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=SETTINGS_PATH,
        help="Path to Claude Code settings.json. Defaults to ~/.claude/settings.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return install(args.settings.expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
