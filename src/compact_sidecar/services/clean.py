from __future__ import annotations

import json
import plistlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from compact_sidecar.hooks import install as install_hooks
from compact_sidecar.services import daemon
from config import SidecarConfigError, load_config_safe, require_file_name
from paths import project_root, runtime_dir as default_runtime_dir


CLEAN_AGENT_LABEL = "com.claude-code-compact-sidecar.daemon"
CLEAN_DOMAIN_PREFIX = "gui"
CLEAN_DAEMON_MODULE = "compact_sidecar.services.daemon"


@dataclass
class CleanPlan:
    settings_path: Path
    plist_path: Path
    runtime_dir: Path
    hooks_removed: int = 0
    settings_exists: bool = False
    plist_exists: bool = False
    runtime_remove: list[Path] = field(default_factory=list)
    runtime_skip: list[Path] = field(default_factory=list)
    launchctl_target: str = ""


def clean_launchctl_domain() -> str:
    return f"{CLEAN_DOMAIN_PREFIX}/{daemon.os.getuid()}"


def clean_launchctl_target() -> str:
    return f"{clean_launchctl_domain()}/{CLEAN_AGENT_LABEL}"


def default_project_settings_path() -> Path:
    return project_root() / ".claude" / "settings.local.json"


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{CLEAN_AGENT_LABEL}.plist"


def runtime_file_names(config: dict[str, Any]) -> set[str]:
    names = {require_file_name(str(name), "runtime file") for name in config["paths"]["runtime_files"].values()}
    names.add(require_file_name("sidecar.config.json", "runtime file"))
    return names


def is_summary_backup(path: Path, config: dict[str, Any]) -> bool:
    prefix = require_file_name(str(config["paths"]["summary_backup_prefix"]), "summary backup prefix")
    return path.name.startswith(f"{prefix}.") and path.name.endswith(".md")


def collect_runtime_targets(directory: Path, config: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    if not directory.exists():
        return [], []
    allowed_names = runtime_file_names(config)
    removable: list[Path] = []
    skipped: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda path: path.name):
        if child.is_file() and (child.name in allowed_names or is_summary_backup(child, config)):
            removable.append(child)
        else:
            skipped.append(child)
    return removable, skipped


def collect_clean_plan(settings_path: Path | None, plist_path: Path | None, runtime_directory: Path | None, config_path: str | None = None) -> CleanPlan:
    config = load_config_safe(config_path)
    active_settings = (settings_path or default_project_settings_path()).expanduser()
    active_plist = (plist_path or default_plist_path()).expanduser()
    active_runtime = (runtime_directory or default_runtime_dir()).expanduser()
    runtime_remove, runtime_skip = collect_runtime_targets(active_runtime, config)
    removed = 0
    settings_exists = active_settings.exists()
    if settings_exists:
        settings, removed = install_hooks.remove_sidecar_hooks(install_hooks.load_settings(active_settings), config)
        _ = settings
    return CleanPlan(
        settings_path=active_settings,
        plist_path=active_plist,
        runtime_dir=active_runtime,
        hooks_removed=removed,
        settings_exists=settings_exists,
        plist_exists=active_plist.exists(),
        runtime_remove=runtime_remove,
        runtime_skip=runtime_skip,
        launchctl_target=clean_launchctl_target(),
    )


def safe_error_message(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def error_payload(message: str, *, json_output: bool) -> None:
    payload = {"error": message}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"error={message}")


def remove_hooks(settings_path: Path, config: dict[str, Any]) -> int:
    if not settings_path.exists():
        return 0
    settings, removed = install_hooks.remove_sidecar_hooks(install_hooks.load_settings(settings_path), config)
    install_hooks.write_settings(settings_path, install_hooks.dump_settings(settings))
    return removed


def clean_plist_is_valid(plist: dict[str, Any]) -> bool:
    program_arguments = plist.get("ProgramArguments")
    if not isinstance(program_arguments, list):
        program_arguments = []
    program_argument_text = [str(part) for part in program_arguments]
    environment = plist.get("EnvironmentVariables")
    if not isinstance(environment, dict):
        environment = {}
    return bool(
        plist.get("Label") == CLEAN_AGENT_LABEL
        and len(program_argument_text) >= 3
        and program_argument_text[1:3] == ["-m", CLEAN_DAEMON_MODULE]
        and "--loop" in program_argument_text
        and "--interval-seconds" in program_argument_text
        and isinstance(plist.get("WorkingDirectory"), str)
        and isinstance(environment.get("SIDECAR_COMPACT_DIR"), str)
        and plist.get("RunAtLoad") is not True
        and plist.get("KeepAlive") is not True
    )


def remove_valid_sidecar_plist(plist_path: Path) -> tuple[str, int, bool]:
    if not plist_path.exists():
        return "absent", 0, False
    try:
        plist = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return "invalid", 1, False
    if not isinstance(plist, dict):
        return "invalid", 1, False
    if not clean_plist_is_valid(plist):
        return "refused", 1, False
    try:
        plist_path.unlink()
    except OSError:
        return "unlink-failed", 1, False
    return "removed", 0, True


def bootout_fixed_label() -> tuple[str, int | None]:
    result = daemon.launchctl_bootout_fixed_label()
    if isinstance(result, OSError):
        return "error", 1
    if result.returncode == 0:
        return "ok", 0
    if result.returncode in {3, 113}:
        return "absent", result.returncode
    return "failed", result.returncode


def terminate_residual_verified_pid() -> dict[str, Any]:
    result = daemon.launchctl_print_fixed_label()
    if isinstance(result, OSError) or result.returncode != 0:
        return {"terminated_pid": None, "pid_signal_sent": False}
    pid = daemon.verified_pid_from_launchctl_output(result.stdout)
    if pid is None:
        return {"terminated_pid": None, "pid_signal_sent": False}
    signal_sent = daemon.terminate_verified_pid(pid)
    return {"terminated_pid": pid, "pid_signal_sent": signal_sent}


def remove_runtime_files(paths: list[Path], config: dict[str, Any]) -> tuple[int, list[str]]:
    allowed_names = runtime_file_names(config)
    removed = 0
    failed: list[str] = []
    for path in paths:
        try:
            allowed = path.name in allowed_names or is_summary_backup(path, config)
            if not allowed or not path.is_file():
                failed.append(path.name)
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(path.name)
            continue
        removed += 1
    return removed, failed


def apply_clean_plan(plan: CleanPlan, config_path: str | None = None) -> tuple[dict[str, Any], int]:
    bootout_status, bootout_returncode = bootout_fixed_label()
    if bootout_status not in {"ok", "absent"}:
        return (
            {
                "hooks_removed": 0,
                "launchctl_bootout": bootout_status,
                "launchctl_returncode": bootout_returncode,
                "terminated_pid": None,
                "pid_signal_sent": False,
                "plist_removed": False,
                "plist_status": "not-attempted",
                "plist_exit_code": None,
                "runtime_removed": 0,
                "runtime_skipped": len(plan.runtime_skip),
                "runtime_failed": [],
            },
            int(bootout_returncode or 1),
        )

    pid_result = terminate_residual_verified_pid()
    plist_status, plist_exit, plist_removed = remove_valid_sidecar_plist(plan.plist_path)
    if plist_exit != 0:
        return (
            {
                "hooks_removed": 0,
                "launchctl_bootout": bootout_status,
                "launchctl_returncode": bootout_returncode,
                **pid_result,
                "plist_removed": plist_removed,
                "plist_status": plist_status,
                "plist_exit_code": plist_exit,
                "runtime_removed": 0,
                "runtime_skipped": len(plan.runtime_skip),
                "runtime_failed": [],
            },
            plist_exit,
        )

    config = load_config_safe(config_path)
    hooks_removed = remove_hooks(plan.settings_path, config)
    runtime_removed, runtime_failed = remove_runtime_files(plan.runtime_remove, config)
    return (
        {
            "hooks_removed": hooks_removed,
            "launchctl_bootout": bootout_status,
            "launchctl_returncode": bootout_returncode,
            **pid_result,
            "plist_removed": plist_removed,
            "plist_status": plist_status,
            "plist_exit_code": plist_exit,
            "runtime_removed": runtime_removed,
            "runtime_skipped": len(plan.runtime_skip),
            "runtime_failed": runtime_failed,
        },
        1 if runtime_failed else 0,
    )


def plan_payload(plan: CleanPlan, *, dry_run: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dry_run": dry_run,
        "settings_path": str(plan.settings_path),
        "settings_exists": plan.settings_exists,
        "hooks_removed": result.get("hooks_removed", plan.hooks_removed) if result else plan.hooks_removed,
        "plist_path": str(plan.plist_path),
        "plist_exists": plan.plist_exists,
        "launchctl_target": plan.launchctl_target,
        "runtime_dir": str(plan.runtime_dir),
        "runtime_remove": [path.name for path in plan.runtime_remove],
        "runtime_skip": [path.name for path in plan.runtime_skip],
    }
    if result is not None:
        payload.update(result)
    return payload


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        "Sidecar clean",
        f"dry_run={'yes' if payload['dry_run'] else 'no'}",
        f"settings_path={payload['settings_path']}",
        f"hooks_removed={payload['hooks_removed']}",
        f"plist_path={payload['plist_path']}",
        f"launchctl_target={payload['launchctl_target']}",
        f"runtime_dir={payload['runtime_dir']}",
        "runtime_remove=" + ",".join(payload["runtime_remove"]),
        "runtime_skip=" + ",".join(payload["runtime_skip"]),
    ]
    if not payload["dry_run"]:
        lines.extend(
            [
                f"launchctl_bootout={payload['launchctl_bootout']}",
                f"plist_removed={'yes' if payload['plist_removed'] else 'no'}",
                f"plist_status={payload['plist_status']}",
                f"runtime_removed={payload['runtime_removed']}",
                f"runtime_skipped={payload['runtime_skipped']}",
                "runtime_failed=" + ",".join(payload.get("runtime_failed", [])),
            ]
        )
        if payload.get("terminated_pid") is not None:
            lines.append(f"terminated_pid={payload['terminated_pid']}")
    return "\n".join(lines) + "\n"


def run_clean(
    *,
    settings_path: Path | None,
    plist_path: Path | None,
    runtime_directory: Path | None,
    force: bool,
    json_output: bool,
    config_path: str | None = None,
) -> int:
    try:
        plan = collect_clean_plan(settings_path, plist_path, runtime_directory, config_path)
        result: dict[str, Any] | None = None
        exit_code = 0
        if force:
            result, exit_code = apply_clean_plan(plan, config_path)
        payload = plan_payload(plan, dry_run=not force, result=result)
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_text(payload), end="")
        return exit_code
    except (install_hooks.SettingsError, SidecarConfigError, OSError) as exc:
        error_payload(safe_error_message(exc), json_output=json_output)
        return 1
