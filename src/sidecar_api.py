from __future__ import annotations

import contextlib
import io
import os
import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import auto_compact_controller
import daemon
import dashboard
import install_hooks
import merge_compact_history
import operation_log
import readiness
import status
from sidecar_config import CONFIG_PATH_ENV, SidecarConfigError, load_config
from sidecar_paths import ENV_RUNTIME_DIR


@contextmanager
def _scoped_config(config_path: str | Path | None = None, runtime_dir: str | Path | None = None) -> Iterator[None]:
    previous = os.environ.get(CONFIG_PATH_ENV)
    previous_runtime = os.environ.get(ENV_RUNTIME_DIR)
    active_config_path = str(config_path) if config_path else None
    if active_config_path:
        os.environ[CONFIG_PATH_ENV] = active_config_path
    if runtime_dir is not None:
        os.environ[ENV_RUNTIME_DIR] = str(runtime_dir)
    try:
        status.refresh_config(active_config_path, strict=active_config_path is not None)
        dashboard.refresh_config(active_config_path, strict=active_config_path is not None)
        operation_log.refresh_config(active_config_path, strict=active_config_path is not None)
        daemon.refresh_config(active_config_path)
        auto_compact_controller.refresh_config(active_config_path)
        merge_compact_history.refresh_config(active_config_path)
        yield
    finally:
        if previous is None:
            os.environ.pop(CONFIG_PATH_ENV, None)
        else:
            os.environ[CONFIG_PATH_ENV] = previous
        if previous_runtime is None:
            os.environ.pop(ENV_RUNTIME_DIR, None)
        else:
            os.environ[ENV_RUNTIME_DIR] = previous_runtime
        restored = previous or None
        status.refresh_config(restored, strict=False)
        dashboard.refresh_config(restored, strict=False)
        operation_log.refresh_config(restored, strict=False)
        daemon.refresh_config(restored)
        auto_compact_controller.refresh_config(restored)
        merge_compact_history.refresh_config(restored)


def _expanded_path(value: str | Path | None, label: str) -> Path:
    if value is None or not str(value).strip():
        raise ValueError(f"{label} is required")
    return Path(str(value)).expanduser()


def _capture_output(func: Any, *args: Any) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = func(*args)
    return int(exit_code), stdout.getvalue().strip(), stderr.getvalue().strip()


def _capture_stdout(func: Any, *args: Any) -> tuple[int, str]:
    exit_code, stdout, _ = _capture_output(func, *args)
    return exit_code, stdout


def _require_confirm(confirm: bool) -> None:
    if confirm is not True:
        raise ValueError("confirm must be true")


def _is_global_settings_path(path: Path) -> bool:
    return path.expanduser() == Path.home() / ".claude" / "settings.json"


def _validated_settings_path(settings_path: str | Path | None, *, allow_global_settings: bool) -> Path:
    settings = _expanded_path(settings_path, "settings_path")
    if _is_global_settings_path(settings) and not allow_global_settings:
        raise ValueError("global settings path requires allow_global_settings=true")
    return settings


def _state_artifact(runtime: Path) -> dict[str, Any]:
    return _file_artifact(runtime / daemon.STATE_NAME)


def _operation_log_artifact(runtime: Path) -> dict[str, Any]:
    return _file_artifact(runtime / operation_log.OPERATION_LOG)


def _safe_text_result(exit_code: int, stdout: str, stderr: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"exit_code": exit_code, "stdout": stdout}
    if stderr:
        result["stderr"] = stderr
    return result


def _file_artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0}


def _warning_list(*extra: str) -> list[str]:
    return ["launchctl was not invoked", "tmux was not invoked", *extra]


def _next_command(parts: list[str]) -> str:
    return shlex.join(parts)


def status_snapshot(config_path: str | Path | None = None) -> dict[str, Any]:
    with _scoped_config(config_path):
        files = status.inspect_runtime()
        return {
            "runtime_dir": str(status.runtime_dir()),
            "status": status.final_status(files),
            "readiness": status.compact_readiness(files),
            "files": files,
        }


def dashboard_snapshot(
    config_path: str | Path | None = None,
    *,
    log_limit: int | None = None,
    show_content: bool = False,
) -> dict[str, Any]:
    with _scoped_config(config_path):
        limit = dashboard.DEFAULT_LOG_LIMIT if log_limit is None else log_limit
        snapshot = dashboard.build_dashboard_snapshot(log_limit=limit)
        return dashboard.snapshot_for_output(snapshot, show_content=show_content)


def validate_config(config_path: str | Path | None = None) -> dict[str, Any]:
    try:
        config = load_config(config_path)
    except SidecarConfigError as exc:
        return {"valid": False, "error": str(exc)}
    return {
        "valid": True,
        "schema_version": config.get("schema_version"),
        "config_path": config.get("_config_path"),
        "runtime_dir": config.get("paths", {}).get("runtime_dir"),
        "api_key_env": config.get("llm", {}).get("api_key_env"),
    }


def hook_setup_preview(config_path: str | Path | None = None) -> dict[str, Any]:
    try:
        config = load_config(config_path)
    except SidecarConfigError as exc:
        return {"valid": False, "error": str(exc), "hooks": []}
    return {
        "valid": True,
        "settings_path": str(Path(str(config["paths"]["claude_settings_path"])).expanduser()),
        "hooks": install_hooks.required_hook_specs(config),
    }


def operation_log_snapshot(
    config_path: str | Path | None = None,
    *,
    limit: int | None = None,
    include_rotated: bool = True,
    show_content: bool = False,
) -> dict[str, Any]:
    with _scoped_config(config_path):
        records = operation_log.read_operation_records(limit=limit, include_rotated=include_rotated)
        if not show_content:
            records = [{key: value for key, value in record.items() if key != "raw"} for record in records]
        return {
            "operation_log": operation_log.inspect_operation_log(operation_log.OPERATION_LOG),
            "rotated_operation_log": operation_log.inspect_operation_log(operation_log.ROTATED_OPERATION_LOG),
            "records": records,
        }


def daemon_agent_status(plist_path: str | Path, config_path: str | Path | None = None) -> dict[str, Any]:
    plist = _expanded_path(plist_path, "plist_path")
    with _scoped_config(config_path):
        text, exit_code = daemon.render_agent_status(plist)
    return {"exit_code": exit_code, "text": text}


def setup_rehearsal(
    settings_path: str | Path,
    runtime_dir: str | Path,
    plist_path: str | Path | None = None,
    config_path: str | Path | None = None,
    *,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    settings = _expanded_path(settings_path, "settings_path")
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    plist = Path(str(plist_path)).expanduser() if plist_path is not None and str(plist_path).strip() else None
    artifacts: dict[str, Any] = {"settings": _file_artifact(settings), "runtime_dir": {"path": str(runtime), "exists": runtime.exists()}}
    with _scoped_config(config_path, runtime):
        config = load_config(config_path)
        hook_exit, hook_output = _capture_stdout(install_hooks.install, settings, config)
        artifacts["settings"] = _file_artifact(settings)
        daemon_output = ""
        daemon_exit = 0
        if plist is not None:
            daemon_exit, daemon_output = _capture_stdout(daemon.install_agent, plist, interval_seconds or daemon.DEFAULT_INTERVAL_SECONDS)
            artifacts["plist"] = _file_artifact(plist)
            artifacts["daemon_state"] = _file_artifact(runtime / daemon.STATE_NAME)
        artifacts["runtime_dir"] = {"path": str(runtime), "exists": runtime.exists()}
    return {
        "ok": hook_exit == 0 and daemon_exit == 0,
        "mode": "setup-rehearsal",
        "artifacts": artifacts,
        "warnings": _warning_list("settings were written to the explicit rehearsal path"),
        "next_step_commands": [
            _next_command(["python3", "src/sidecar.py", "setup", "--settings", str(settings), "--no-launchctl"]),
        ],
        "hook_setup": {"exit_code": hook_exit, "text": hook_output},
        "daemon_setup": {"exit_code": daemon_exit, "text": daemon_output} if plist is not None else None,
    }


def daemon_plist_rehearsal(
    plist_path: str | Path,
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    plist = _expanded_path(plist_path, "plist_path")
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    with _scoped_config(config_path, runtime):
        exit_code, output = _capture_stdout(daemon.install_agent, plist, interval_seconds or daemon.DEFAULT_INTERVAL_SECONDS)
        status_text, status_exit = daemon.render_agent_status(plist)
        artifacts = {
            "plist": _file_artifact(plist),
            "runtime_dir": {"path": str(runtime), "exists": runtime.exists()},
            "daemon_state": _file_artifact(runtime / daemon.STATE_NAME),
        }
    return {
        "ok": exit_code == 0 and status_exit == 0,
        "mode": "daemon-plist-rehearsal",
        "artifacts": artifacts,
        "warnings": _warning_list("launchd plist was written to the explicit rehearsal path"),
        "next_step_commands": [
            _next_command(["python3", "src/daemon.py", "--agent-status", "--plist-path", str(plist)]),
        ],
        "daemon_setup": {"exit_code": exit_code, "text": output},
        "agent_status": {"exit_code": status_exit, "text": status_text},
    }


def compact_plan_preview(
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    prompt_chars: int = 0,
    min_readiness: str | None = None,
) -> dict[str, Any]:
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    if prompt_chars < 0:
        raise ValueError("prompt_chars must be at least 0")
    with _scoped_config(config_path, runtime):
        files = status.inspect_runtime()
        runtime_readiness = status.compact_readiness(files)
        estimated_chars = status.estimated_runtime_chars(files) + prompt_chars
        level = readiness.readiness_level(estimated_chars, attention=runtime_readiness["level"] == "attention")
        threshold = min_readiness or auto_compact_controller.DEFAULT_MIN_READINESS
        if threshold not in auto_compact_controller.READINESS_ORDER:
            raise ValueError(f"unknown min_readiness: {threshold}")
        should_compact = auto_compact_controller.READINESS_ORDER[level] >= auto_compact_controller.READINESS_ORDER[threshold]
        actions = ["send compact"] if should_compact else []
        if prompt_chars:
            actions.append("send prompt")
        if not actions:
            actions.append("noop")
    return {
        "ok": True,
        "mode": "compact-plan-preview",
        "artifacts": {"runtime_dir": {"path": str(runtime), "exists": runtime.exists()}},
        "warnings": _warning_list("preview only; no compact command or prompt was sent"),
        "next_step_commands": [
            _next_command(["python3", "src/sidecar.py", "compact", "--no-send", "--min-readiness", threshold]),
        ],
        "plan": {
            "runtime_readiness": runtime_readiness["level"],
            "readiness": level,
            "estimated_chars": estimated_chars,
            "prompt_chars": prompt_chars,
            "min_readiness": threshold,
            "should_compact": should_compact,
            "actions": actions,
            "basis": readiness.READINESS_BASIS,
            "accuracy": readiness.READINESS_ACCURACY,
        },
    }


def hook_install_mutation(
    settings_path: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    allow_global_settings: bool = False,
) -> dict[str, Any]:
    _require_confirm(confirm)
    settings = _validated_settings_path(settings_path, allow_global_settings=allow_global_settings)
    config = load_config(config_path)
    exit_code, output = _capture_stdout(install_hooks.install, settings, config)
    return {
        "ok": exit_code == 0,
        "mode": "hook-install",
        "artifacts": {"settings": _file_artifact(settings)},
        "result": _safe_text_result(exit_code, output),
        "warnings": ["settings were written to the explicit path"],
    }


def hook_uninstall_mutation(
    settings_path: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    allow_global_settings: bool = False,
) -> dict[str, Any]:
    _require_confirm(confirm)
    settings = _validated_settings_path(settings_path, allow_global_settings=allow_global_settings)
    config = load_config(config_path)
    exit_code, output = _capture_stdout(install_hooks.uninstall, settings, config)
    return {
        "ok": exit_code == 0,
        "mode": "hook-uninstall",
        "artifacts": {"settings": _file_artifact(settings)},
        "result": _safe_text_result(exit_code, output),
        "warnings": ["settings were written to the explicit path"],
    }


def daemon_plist_write_mutation(
    plist_path: str | Path,
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    _require_confirm(confirm)
    plist = _expanded_path(plist_path, "plist_path")
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    with _scoped_config(config_path, runtime):
        exit_code, output = _capture_stdout(daemon.install_agent, plist, interval_seconds or daemon.DEFAULT_INTERVAL_SECONDS)
        status_text, status_exit = daemon.render_agent_status(plist)
        artifacts = {"plist": _file_artifact(plist), "runtime_dir": {"path": str(runtime), "exists": runtime.exists()}, "daemon_state": _state_artifact(runtime)}
    return {
        "ok": exit_code == 0 and status_exit == 0,
        "mode": "daemon-plist-write",
        "artifacts": artifacts,
        "result": _safe_text_result(exit_code, output),
        "agent_status": {"exit_code": status_exit, "text": status_text},
        "warnings": ["launchctl was not invoked"],
    }


def daemon_plist_remove_mutation(
    plist_path: str | Path,
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    _require_confirm(confirm)
    plist = _expanded_path(plist_path, "plist_path")
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    with _scoped_config(config_path, runtime):
        exit_code, output = _capture_stdout(daemon.remove_agent, plist)
        artifacts = {"plist": _file_artifact(plist), "runtime_dir": {"path": str(runtime), "exists": runtime.exists()}, "daemon_state": _state_artifact(runtime)}
    return {
        "ok": exit_code == 0,
        "mode": "daemon-plist-remove",
        "artifacts": artifacts,
        "result": _safe_text_result(exit_code, output),
        "warnings": ["launchctl was not invoked"],
    }


def daemon_run_once_mutation(
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    operation_log_enabled: bool = False,
) -> dict[str, Any]:
    _require_confirm(confirm)
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    with _scoped_config(config_path, runtime):
        exit_code, output, error = _capture_output(daemon.run_once)
        daemon.log_llm_summary_operation(operation_log_enabled)
        daemon.log_daemon_operation(operation_log_enabled, "run-once", "ok" if exit_code == 0 else "error", daemon.read_state_metadata())
        artifacts = {
            "runtime_dir": {"path": str(runtime), "exists": runtime.exists()},
            "daemon_state": _state_artifact(runtime),
            "operation_log": _operation_log_artifact(runtime),
        }
    return {
        "ok": exit_code == 0,
        "mode": "daemon-run-once",
        "artifacts": artifacts,
        "result": _safe_text_result(exit_code, output, error),
        "warnings": ["bounded run-once only; daemon loop was not exposed"],
    }


def launchctl_lifecycle_mutation(
    action: str,
    plist_path: str | Path,
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    operation_log_enabled: bool = False,
) -> dict[str, Any]:
    _require_confirm(confirm)
    if action not in {"bootstrap", "kickstart", "status", "bootout"}:
        raise ValueError("action must be one of bootstrap, kickstart, status, bootout")
    plist = _expanded_path(plist_path, "plist_path")
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    with _scoped_config(config_path, runtime):
        exit_code, output = _capture_stdout(daemon.launchctl_lifecycle, plist.resolve(), action)
        daemon.log_daemon_operation(operation_log_enabled, f"launchctl-{action}", "ok" if exit_code == 0 else "error", daemon.read_state_metadata())
        artifacts = {"plist": _file_artifact(plist), "runtime_dir": {"path": str(runtime), "exists": runtime.exists()}, "daemon_state": _state_artifact(runtime)}
    return {
        "ok": exit_code == 0,
        "mode": f"launchctl-{action}",
        "artifacts": artifacts,
        "result": _safe_text_result(exit_code, output),
        "warnings": ["launchctl lifecycle was explicitly requested"],
    }


def tmux_compact_mutation(
    runtime_dir: str | Path,
    config_path: str | Path | None = None,
    *,
    confirm: bool = False,
    pane: str | None = None,
    prompt_path: str | Path | None = None,
    no_send: bool = True,
    tmux_path: str | Path | None = None,
    operation_log_enabled: bool = False,
    log_raw_prompt: bool = False,
    min_readiness: str | None = None,
) -> dict[str, Any]:
    _require_confirm(confirm)
    runtime = _expanded_path(runtime_dir, "runtime_dir")
    prompt_file = Path(str(prompt_path)).expanduser() if prompt_path is not None and str(prompt_path).strip() else None
    if log_raw_prompt and not operation_log_enabled:
        raise ValueError("log_raw_prompt requires operation_log_enabled=true")
    if not no_send and not pane:
        raise ValueError("pane is required when no_send=false")
    with _scoped_config(config_path, runtime):
        threshold = min_readiness or auto_compact_controller.DEFAULT_MIN_READINESS
        if threshold not in auto_compact_controller.READINESS_ORDER:
            raise ValueError(f"unknown min_readiness: {threshold}")
        controller_config = auto_compact_controller.ControllerConfig(
            pane=pane,
            confirm_send=True,
            no_send=no_send,
            prompt_file=prompt_file,
            prompt_stdin=False,
            min_readiness=threshold,
            wait_postcompact=False,
            wait_timeout_seconds=auto_compact_controller.DEFAULT_WAIT_TIMEOUT_SECONDS,
            poll_interval_seconds=auto_compact_controller.DEFAULT_POLL_INTERVAL_SECONDS,
            merge_after=False,
            tmux_path=str(tmux_path) if tmux_path is not None and str(tmux_path).strip() else auto_compact_controller.DEFAULT_TMUX_PATH,
            operation_log=operation_log_enabled,
            log_raw_prompt=log_raw_prompt,
        )
        exit_code, output, error = _capture_output(auto_compact_controller.run_controller, controller_config)
        artifacts = {"runtime_dir": {"path": str(runtime), "exists": runtime.exists()}, "operation_log": _operation_log_artifact(runtime)}
    return {
        "ok": exit_code == 0,
        "mode": "tmux-compact",
        "artifacts": artifacts,
        "result": _safe_text_result(exit_code, output, error),
        "warnings": ["tmux send was disabled" if no_send else "tmux send was explicitly requested"],
    }
