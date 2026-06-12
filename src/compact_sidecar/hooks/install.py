from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from config import CONFIG_PATH_ENV, SidecarConfigError, config_path_env, copy_default_config_to_runtime, load_config_for_import, load_config_safe, load_template, print_config_error, require_file_name, runtime_config_path, source_tree_pythonpath

_CONFIG = load_config_for_import()
_PATHS = _CONFIG["paths"]
_TEMPLATE_PATHS = load_template()["paths"]
SETTINGS_PATH = Path(str(_PATHS["claude_settings_path"])).expanduser()
PYTHON = str(_PATHS["python_executable"])
USERPROMPT_SCRIPT = str(_PATHS["scripts"]["userprompt"])
POSTCOMPACT_SCRIPT = str(_PATHS["scripts"]["postcompact"])


class SettingsError(Exception):
    pass


def effective_python(active_config: dict[str, Any]) -> str:
    configured = str(active_config["paths"]["python_executable"])
    return sys.executable if configured == str(_TEMPLATE_PATHS["python_executable"]) else configured


def module_name_for_script(script_name: str) -> str | None:
    return {
        USERPROMPT_SCRIPT: "compact_sidecar.hooks.userprompt",
        POSTCOMPACT_SCRIPT: "compact_sidecar.hooks.postcompact",
    }.get(script_name)


def command_env(active_config: dict[str, Any]) -> dict[str, str]:
    env = dict(config_path_env(active_config))
    if CONFIG_PATH_ENV not in env:
        env[CONFIG_PATH_ENV] = str(runtime_config_path(config=active_config))
    pythonpath = source_tree_pythonpath()
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    return env


def shell_assignments(env: dict[str, str]) -> str:
    return " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())


def script_command(script_name: str, config: dict[str, Any] | None = None) -> str:
    active_config = _CONFIG if config is None else config
    script = require_file_name(script_name, "hook script")
    module_name = module_name_for_script(script)
    argv = [effective_python(active_config), "-m", module_name] if module_name else [str(active_config["paths"]["python_executable"]), script]
    env = command_env(active_config)
    if CONFIG_PATH_ENV in env:
        argv = [*argv, "--config", env[CONFIG_PATH_ENV]]
    command = shlex.join(argv)
    prefix = shell_assignments(env)
    return f"{prefix} {command}" if prefix else command


def required_hook_specs(config: dict[str, Any] | None = None) -> list[dict[str, str | int]]:
    active_config = _CONFIG if config is None else config
    specs: list[dict[str, str | int]] = []
    for entry in active_config["hooks"]["entries"]:
        script = str(entry["script"])
        specs.append(
            {
                "event": str(entry["event"]),
                "matcher": str(entry["matcher"]),
                "script": script,
                "command": script_command(script, active_config),
                "timeout": int(entry["timeout"]),
                "statusMessage": str(entry["status_message"]),
            }
        )
    return specs


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

    if any(Path(part).name == script_name for part in parts):
        return True

    module_name = module_name_for_script(script_name)
    return module_name is not None and any(
        part == "-m" and index + 1 < len(parts) and parts[index + 1] == module_name
        for index, part in enumerate(parts)
    )


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


def merge_hooks(settings: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError("settings.hooks must be a JSON object")

    for spec in required_hook_specs(config):
        event = str(spec["event"])
        matcher = str(spec["matcher"])
        script = str(spec["script"])
        event_hooks = hooks.setdefault(event, [])
        require_list(event_hooks, f"settings.hooks.{event}")
        entry = matcher_entry_for(event_hooks, matcher)
        entry_hooks = require_list(entry.setdefault("hooks", []), "hook entry hooks")

        if has_sidecar_hook(entry_hooks, script):
            continue

        entry_hooks.append(
            {
                "type": "command",
                "command": str(spec["command"]),
                "timeout": int(spec["timeout"]),
                "statusMessage": str(spec["statusMessage"]),
            }
        )

    return settings


def remove_sidecar_hooks(settings: dict[str, Any], config: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    hooks = settings.get("hooks")
    if hooks is None:
        return settings, 0
    if not isinstance(hooks, dict):
        raise SettingsError("settings.hooks must be a JSON object")

    removed = 0
    for spec in required_hook_specs(config):
        event = str(spec["event"])
        matcher = str(spec["matcher"])
        script = str(spec["script"])
        event_hooks = hooks.get(event)
        if event_hooks is None:
            continue
        require_list(event_hooks, f"settings.hooks.{event}")
        kept_entries = []
        for entry in event_hooks:
            if not isinstance(entry, dict):
                raise SettingsError("hook event entries must be JSON objects")
            entry_hooks = require_list(entry.get("hooks", []), "hook entry hooks")
            if entry.get("matcher", "") != matcher:
                kept_entries.append(entry)
                continue
            kept_hooks = []
            for hook in entry_hooks:
                if not isinstance(hook, dict):
                    raise SettingsError("hook commands must be JSON objects")
                command = hook.get("command")
                is_sidecar = hook.get("type") == "command" and command_references_script(command, script)
                if is_sidecar:
                    removed += 1
                else:
                    kept_hooks.append(hook)
            if kept_hooks:
                updated_entry = dict(entry)
                updated_entry["hooks"] = kept_hooks
                kept_entries.append(updated_entry)
        if kept_entries:
            hooks[event] = kept_entries
        else:
            hooks.pop(event, None)

    if not hooks:
        settings.pop("hooks", None)
    return settings, removed


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


def install(settings_path: Path, config: dict[str, Any] | None = None) -> int:
    try:
        settings = merge_hooks(load_settings(settings_path), config)
        runtime_config, created_config = copy_default_config_to_runtime(config)
    except (SettingsError, SidecarConfigError) as exc:
        print(f"compact_sidecar.hooks.install: {exc}", file=sys.stderr)
        return 1

    output = dump_settings(settings)
    try:
        write_settings(settings_path, output)
    except SettingsError as exc:
        print(f"compact_sidecar.hooks.install: {exc}", file=sys.stderr)
        return 1

    print(f"Installed sidecar hooks into {settings_path}")
    print(f"runtime_config: {runtime_config} created={'yes' if created_config else 'no'}")
    return 0


def uninstall(settings_path: Path, config: dict[str, Any] | None = None) -> int:
    if not settings_path.exists():
        print(f"Removed 0 sidecar hooks from {settings_path}")
        return 0

    try:
        settings, removed = remove_sidecar_hooks(load_settings(settings_path), config)
    except SettingsError as exc:
        print(f"compact_sidecar.hooks.install: {exc}", file=sys.stderr)
        return 1

    output = dump_settings(settings)
    try:
        write_settings(settings_path, output)
    except SettingsError as exc:
        print(f"compact_sidecar.hooks.install: {exc}", file=sys.stderr)
        return 1

    print(f"Removed {removed} sidecar hooks from {settings_path}")
    return 0


def parse_args(argv: list[str] | None = None, config: dict[str, Any] | None = None) -> argparse.Namespace:
    active_config = _CONFIG if config is None else config
    default_settings_path = Path(str(active_config["paths"]["claude_settings_path"])).expanduser()
    parser = argparse.ArgumentParser(description="Install Claude Code sidecar compact hooks.")
    parser.add_argument("--config", help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=default_settings_path,
        help="Path to Claude Code settings.json. Defaults to ~/.claude/settings.json.",
    )
    parser.add_argument(
        "--confirm-user-settings",
        action="store_true",
        help="Compatibility no-op; default ~/.claude/settings.json writes are allowed.",
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove sidecar hooks from settings instead of installing them.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    active_argv = sys.argv[1:] if argv is None else argv
    pre_args = argparse.ArgumentParser(add_help=False)
    pre_args.add_argument("--config")
    config_args, _ = pre_args.parse_known_args(active_argv)
    active_config_path = config_args.config or os.environ.get(CONFIG_PATH_ENV, "").strip() or None
    if config_args.config:
        os.environ[CONFIG_PATH_ENV] = config_args.config
    try:
        config = load_config_safe(active_config_path)
    except SidecarConfigError as exc:
        print_config_error("compact_sidecar.hooks.install", exc)
        return 1
    args = parse_args(active_argv, config)
    settings_path = args.settings.expanduser()
    if args.uninstall:
        return uninstall(settings_path, config)
    return install(settings_path, config)


if __name__ == "__main__":
    raise SystemExit(main())
