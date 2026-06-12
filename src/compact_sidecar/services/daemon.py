from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from compact_sidecar.runtime import merge_compact_history
from compact_sidecar.runtime import operation_log
from compact_sidecar.runtime import rolling_summary_writer
from compact_sidecar.services.llm_summarizer import LLMSummaryConfig, LLMSummaryConfigError, LLMSummaryRequestError, summarize_with_openai_compatible
from compact_sidecar.runtime.memory_candidates import collect_recent_candidates
from compact_sidecar.runtime.operation_log import append_operation
from compact_sidecar.runtime.rolling_summary_writer import RollingSummaryError, write_rolling_summary_with_backup
from config import (
    CONFIG_PATH_ENV,
    SidecarConfigError,
    cli_config_path,
    config_path_env,
    load_config,
    load_config_for_import,
    load_config_safe,
    load_template,
    print_config_error,
    require_env_name,
    require_secret_safe_endpoint,
    source_tree_pythonpath,
)
from paths import ENV_RUNTIME_DIR, project_root, runtime_dir, runtime_path

_CONFIG = load_config_for_import()
_PATHS = _CONFIG["paths"]
_TEMPLATE_PATHS = load_template()["paths"]
_LAUNCHD_CONFIG = _CONFIG["daemon_launchd"]
_ENVIRONMENT_CONFIG = _CONFIG["environment"]
STATE_NAME = str(_LAUNCHD_CONFIG["state_file"])
AGENT_LABEL = str(_LAUNCHD_CONFIG["agent_label"])
DEFAULT_INTERVAL_SECONDS = int(_LAUNCHD_CONFIG["default_interval_seconds"])
ENV_LAUNCHCTL_PATH = str(_ENVIRONMENT_CONFIG["launchctl_path"])
DEFAULT_LAUNCHCTL_PATH = str(_LAUNCHD_CONFIG["launchctl_path"])
DEFAULT_API_KEY_ENV = str(_CONFIG["llm"]["api_key_env"])
PYTHON_EXECUTABLE = str(_PATHS["python_executable"])
DEFAULT_PYTHON_EXECUTABLE = PYTHON_EXECUTABLE
DAEMON_MODULE = "compact_sidecar.services.daemon"
FIXED_AGENT_LABEL = "com.claude-code-compact-sidecar.daemon"
FIXED_DOMAIN_PREFIX = "gui"
DAEMON_STDOUT = str(_LAUNCHD_CONFIG["stdout_file"])
DAEMON_STDERR = str(_LAUNCHD_CONFIG["stderr_file"])
PLIST_FILE_MODE = int(str(_LAUNCHD_CONFIG["plist_file_mode"]), 8)
RUN_AT_LOAD = bool(_LAUNCHD_CONFIG["run_at_load"])
KEEP_ALIVE = bool(_LAUNCHD_CONFIG["keep_alive"])
DOMAIN_PREFIX = str(_LAUNCHD_CONFIG["domain_prefix"])
DEFAULT_OPERATION_LOG = bool(_CONFIG["operation_log"].get("enabled_by_default"))
LLM_ENV_NAMES = (
    "SIDECAR_LLM_ENDPOINT",
    "SIDECAR_LLM_MODEL",
    "SIDECAR_LLM_API_KEY_ENV",
    "SIDECAR_LLM_TIMEOUT_SECONDS",
    "SIDECAR_LLM_MAX_INPUT_CHARS",
    "SIDECAR_LLM_MAX_OUTPUT_CHARS",
)


def refresh_config(config_path: str | None = None) -> None:
    global _CONFIG, _PATHS, _LAUNCHD_CONFIG, _ENVIRONMENT_CONFIG
    global STATE_NAME, AGENT_LABEL, DEFAULT_INTERVAL_SECONDS, ENV_LAUNCHCTL_PATH, DEFAULT_LAUNCHCTL_PATH
    global DEFAULT_API_KEY_ENV, PYTHON_EXECUTABLE, DAEMON_STDOUT, DAEMON_STDERR, PLIST_FILE_MODE
    global RUN_AT_LOAD, KEEP_ALIVE, DOMAIN_PREFIX, DEFAULT_OPERATION_LOG

    _CONFIG = load_config_safe(config_path)
    merge_compact_history.refresh_config(config_path)
    operation_log.refresh_config(config_path)
    rolling_summary_writer.refresh_config(config_path)
    _PATHS = _CONFIG["paths"]
    _LAUNCHD_CONFIG = _CONFIG["daemon_launchd"]
    _ENVIRONMENT_CONFIG = _CONFIG["environment"]
    STATE_NAME = str(_LAUNCHD_CONFIG["state_file"])
    AGENT_LABEL = str(_LAUNCHD_CONFIG["agent_label"])
    DEFAULT_INTERVAL_SECONDS = int(_LAUNCHD_CONFIG["default_interval_seconds"])
    ENV_LAUNCHCTL_PATH = str(_ENVIRONMENT_CONFIG["launchctl_path"])
    DEFAULT_LAUNCHCTL_PATH = str(_LAUNCHD_CONFIG["launchctl_path"])
    DEFAULT_API_KEY_ENV = str(_CONFIG["llm"]["api_key_env"])
    PYTHON_EXECUTABLE = str(_PATHS["python_executable"])
    DAEMON_STDOUT = str(_LAUNCHD_CONFIG["stdout_file"])
    DAEMON_STDERR = str(_LAUNCHD_CONFIG["stderr_file"])
    PLIST_FILE_MODE = int(str(_LAUNCHD_CONFIG["plist_file_mode"]), 8)
    RUN_AT_LOAD = bool(_LAUNCHD_CONFIG["run_at_load"])
    KEEP_ALIVE = bool(_LAUNCHD_CONFIG["keep_alive"])
    DOMAIN_PREFIX = str(_LAUNCHD_CONFIG["domain_prefix"])
    DEFAULT_OPERATION_LOG = bool(_CONFIG["operation_log"].get("enabled_by_default"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(payload: dict[str, Any]) -> None:
    state_path = runtime_path(STATE_NAME)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_state_metadata() -> dict[str, Any]:
    state_path = runtime_path(STATE_NAME)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def llm_operation_metadata(state: dict[str, Any]) -> dict[str, Any]:
    key_map = {
        "llm_provider": "provider",
        "llm_model": "model",
        "llm_prompt_tokens": "prompt_tokens",
        "llm_completion_tokens": "completion_tokens",
        "llm_total_tokens": "total_tokens",
        "llm_elapsed_ms": "elapsed_ms",
        "llm_input_chars": "input_chars",
        "llm_output_chars": "output_chars",
        "llm_candidate_count": "candidate_count",
        "summary_written": "summary_written",
        "summary_backup": "summary_backup",
        "error_kind": "error_kind",
        "llm_summary_skipped": "skipped",
        "llm_last_success_provider": "last_success_provider",
        "llm_last_success_model": "last_success_model",
        "llm_last_success_prompt_tokens": "last_success_prompt_tokens",
        "llm_last_success_completion_tokens": "last_success_completion_tokens",
        "llm_last_success_total_tokens": "last_success_total_tokens",
        "llm_last_success_elapsed_ms": "last_success_elapsed_ms",
        "llm_last_success_input_chars": "last_success_input_chars",
        "llm_last_success_output_chars": "last_success_output_chars",
        "llm_last_success_candidate_count": "last_success_candidate_count",
    }
    metadata: dict[str, Any] = {}
    for source, target in key_map.items():
        if source in state:
            metadata[target] = state[source]
    return metadata


def log_llm_summary_operation(enabled: bool, state: dict[str, Any] | None = None) -> None:
    if not enabled:
        return
    state = read_state_metadata() if state is None else state
    status = state.get("llm_summary_status")
    if status not in ("ok", "error", "skipped"):
        return
    append_operation(
        "daemon",
        "llm-summary",
        str(status),
        metadata=llm_operation_metadata(state),
        content_policy={"raw_prompt_logged": False, "raw_summary_logged": False},
    )


def log_daemon_operation(enabled: bool, operation: str, status: str, metadata: dict[str, Any] | None = None) -> None:
    if not enabled:
        return
    append_operation(
        "daemon",
        operation,
        status,
        metadata=metadata or {},
        content_policy={"raw_prompt_logged": False, "raw_summary_logged": False},
    )


def run_once_payload(candidate_count: int, draft_path: Path, llm_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": utc_now(),
        "mode": "run-once",
        "candidate_count": candidate_count,
        "draft_path": str(draft_path),
        "draft_written": True,
    }
    if llm_metadata:
        payload.update(llm_metadata)
    return payload


def llm_prompt_from_candidates(candidates: list[Any]) -> str:
    lines = [
        "Summarize these Claude Code compact history summaries into this exact markdown structure:",
        "# Rolling Summary",
        "",
        "## 当前目标",
        "",
        "## 已确认决策",
        "",
        "## 活动任务",
        "",
        "## 重要约束",
        "",
        "## 未解决问题",
        "",
        "## Compact 前必须保留",
        "",
        "Do not include secrets unless they are explicitly required continuity markers.",
        "",
    ]
    for candidate in candidates:
        lines.extend([f"Source: {candidate.source_file}", f"Timestamp: {candidate.timestamp}", candidate.text, ""])
    return "\n".join(lines)


def llm_summary_metadata_from_result(result: Any, summary_path: Path, backup_path: Path | None, candidate_count: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "llm_summary_status": "ok",
        "llm_provider": result.provider,
        "llm_model": result.model,
        "llm_prompt_tokens": result.prompt_tokens,
        "llm_completion_tokens": result.completion_tokens,
        "llm_total_tokens": result.total_tokens,
        "llm_elapsed_ms": result.elapsed_ms,
        "llm_input_chars": result.input_chars,
        "llm_output_chars": result.output_chars,
        "llm_candidate_count": candidate_count,
        "summary_written": str(summary_path),
    }
    if backup_path is not None:
        metadata["summary_backup"] = str(backup_path)
    return metadata


def last_success_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get("llm_summary_status") != "ok":
        return {}
    return {f"llm_last_success_{key.removeprefix('llm_')}": value for key, value in metadata.items() if key.startswith("llm_")}


def metadata_with_last_success(metadata: dict[str, Any], last_success: dict[str, Any]) -> dict[str, Any]:
    combined = dict(metadata)
    combined.update(last_success)
    return combined


def candidate_signature(candidates: list[Any]) -> str:
    payload = [
        {
            "source_file": str(candidate.source_file),
            "timestamp": str(candidate.timestamp),
            "text": str(candidate.text),
        }
        for candidate in candidates
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_llm_summary_from_history(candidates: list[Any]) -> tuple[dict[str, Any], int]:
    if not candidates:
        return {"llm_summary_status": "skipped", "llm_summary_skipped": "no_candidates"}, 0
    try:
        config = LLMSummaryConfig.from_env()
        result = summarize_with_openai_compatible(config, llm_prompt_from_candidates(candidates))
        summary_path, backup_path = write_rolling_summary_with_backup(result.summary_text)
    except (LLMSummaryConfigError, LLMSummaryRequestError, RollingSummaryError) as exc:
        return {
            "llm_summary_status": "error",
            "error_kind": type(exc).__name__,
            "llm_candidate_count": len(candidates),
        }, 1
    return llm_summary_metadata_from_result(result, summary_path, backup_path, len(candidates)), 0


def run_once() -> int:
    candidates = collect_recent_candidates(limit=merge_compact_history.MAX_DRAFT_SUMMARIES, service="daemon")
    draft_path = runtime_path(merge_compact_history.DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(merge_compact_history.build_draft(candidates), encoding="utf-8")
    llm_metadata, exit_code = write_llm_summary_from_history(candidates)
    write_state(run_once_payload(len(candidates), draft_path, llm_metadata))

    lines = [
        "Sidecar daemon run-once",
        f"runtime_dir: {runtime_dir()}",
        f"candidate_count: {len(candidates)}",
        f"draft_path: {draft_path}",
        f"llm_summary_status: {llm_metadata.get('llm_summary_status', 'unknown')}",
    ]
    if llm_metadata.get("summary_written"):
        lines.append(f"summary_written: {llm_metadata['summary_written']}")
    if "llm_total_tokens" in llm_metadata:
        lines.append(f"llm_total_tokens: {llm_metadata['llm_total_tokens']}")
    if llm_metadata.get("error_kind"):
        lines.append(f"error_kind: {llm_metadata['error_kind']}")
    print("\n".join(lines))
    return exit_code


def loop_payload(
    candidate_count: int,
    draft_path: Path,
    interval_seconds: int,
    run_count: int,
    shutdown_reason: str,
    llm_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": utc_now(),
        "mode": "loop",
        "candidate_count": candidate_count,
        "draft_path": str(draft_path),
        "draft_written": True,
        "interval_seconds": interval_seconds,
        "run_count": run_count,
        "shutdown_reason": shutdown_reason,
    }
    if llm_metadata:
        payload.update(llm_metadata)
    return payload


def run_loop(interval_seconds: int, max_runs: int | None, operation_log: bool = False) -> int:
    run_count = 0
    last_candidate_count = 0
    last_draft_path = runtime_path(merge_compact_history.DRAFT_NAME)
    last_llm_metadata: dict[str, Any] = {"llm_summary_status": "skipped", "llm_summary_skipped": "not-run"}
    last_llm_success_metadata: dict[str, Any] = {}
    last_llm_signature: str | None = None
    shutdown_reason = "interrupted"

    try:
        while max_runs is None or run_count < max_runs:
            candidates = collect_recent_candidates(limit=merge_compact_history.MAX_DRAFT_SUMMARIES, service="daemon")
            last_candidate_count = len(candidates)
            last_draft_path = runtime_path(merge_compact_history.DRAFT_NAME)
            last_draft_path.parent.mkdir(parents=True, exist_ok=True)
            last_draft_path.write_text(merge_compact_history.build_draft(candidates), encoding="utf-8")
            current_signature = candidate_signature(candidates) if candidates else None
            if current_signature is not None and current_signature == last_llm_signature:
                last_llm_metadata = metadata_with_last_success(
                    {"llm_summary_status": "skipped", "llm_summary_skipped": "unchanged", "llm_candidate_count": len(candidates)},
                    last_llm_success_metadata,
                )
            else:
                last_llm_metadata, _ = write_llm_summary_from_history(candidates)
                if last_llm_metadata.get("llm_summary_status") == "ok":
                    last_llm_signature = current_signature
                    last_llm_success_metadata = last_success_metadata(last_llm_metadata)
                else:
                    last_llm_signature = None
            run_count += 1
            running_payload = loop_payload(last_candidate_count, last_draft_path, interval_seconds, run_count, "running", last_llm_metadata)
            write_state(running_payload)
            log_llm_summary_operation(operation_log, running_payload)
            if max_runs is not None and run_count >= max_runs:
                shutdown_reason = "max-runs"
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        shutdown_reason = "interrupted"

    write_state(loop_payload(last_candidate_count, last_draft_path, interval_seconds, run_count, shutdown_reason, last_llm_metadata))
    print("\n".join(["Sidecar daemon loop", f"runtime_dir: {runtime_dir()}", f"run_count: {run_count}", f"shutdown_reason: {shutdown_reason}", f"llm_summary_status: {last_llm_metadata.get('llm_summary_status', 'unknown')}"]))
    return 0


def effective_python() -> str:
    return sys.executable if PYTHON_EXECUTABLE == str(_TEMPLATE_PATHS["python_executable"]) else PYTHON_EXECUTABLE


def daemon_program_arguments(interval_seconds: int) -> list[str]:
    return [
        effective_python(),
        "-m",
        DAEMON_MODULE,
        "--loop",
        "--interval-seconds",
        str(interval_seconds),
        "--operation-log",
    ]


def validate_launchd_environment() -> None:
    if any(os.environ.get(name) for name in LLM_ENV_NAMES):
        load_config(environ=dict(os.environ))


def launchd_environment() -> dict[str, str]:
    validate_launchd_environment()
    environment = {ENV_RUNTIME_DIR: str(runtime_dir())}
    pythonpath = source_tree_pythonpath()
    if pythonpath is not None:
        environment["PYTHONPATH"] = pythonpath
    environment.update(config_path_env(_CONFIG))
    for name in LLM_ENV_NAMES:
        value = os.environ.get(name)
        if not value:
            continue
        if name == "SIDECAR_LLM_ENDPOINT":
            require_secret_safe_endpoint(value, name)
        elif name == "SIDECAR_LLM_API_KEY_ENV":
            require_env_name(value, name)
        environment[name] = value
    return environment


def build_launchd_plist(interval_seconds: int) -> dict[str, Any]:
    runtime = runtime_dir()
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": daemon_program_arguments(interval_seconds),
        "WorkingDirectory": str(project_root()),
        "EnvironmentVariables": launchd_environment(),
        "RunAtLoad": RUN_AT_LOAD,
        "KeepAlive": KEEP_ALIVE,
        "StandardOutPath": str(runtime / DAEMON_STDOUT),
        "StandardErrorPath": str(runtime / DAEMON_STDERR),
    }


def read_plist(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return plist if isinstance(plist, dict) else None


def bool_text(value: object) -> str:
    return "yes" if value else "no"


def program_invokes_current_daemon(program_arguments: list[str]) -> bool:
    return len(program_arguments) >= 3 and bool(program_arguments[0]) and program_arguments[1:3] == ["-m", DAEMON_MODULE]


def program_invokes_legacy_daemon(program_arguments: list[str]) -> bool:
    return len(program_arguments) >= 2 and bool(program_arguments[0]) and Path(program_arguments[1]).name == "daemon.py"


def program_invokes_daemon(program_arguments: list[str]) -> bool:
    return program_invokes_current_daemon(program_arguments) or program_invokes_legacy_daemon(program_arguments)


def plist_metadata(plist: dict[str, Any]) -> dict[str, Any]:
    program_arguments = plist.get("ProgramArguments")
    if not isinstance(program_arguments, list):
        program_arguments = []
    program_argument_text = [str(part) for part in program_arguments]
    environment = plist.get("EnvironmentVariables")
    if not isinstance(environment, dict):
        environment = {}

    label = plist.get("Label")
    return {
        "label": label if isinstance(label, str) else "",
        "label_match": label == AGENT_LABEL,
        "program_daemon": program_invokes_daemon(program_argument_text),
        "program_current_daemon": program_invokes_current_daemon(program_argument_text),
        "program_legacy_daemon": program_invokes_legacy_daemon(program_argument_text),
        "program_loop": "--loop" in program_argument_text,
        "program_interval": "--interval-seconds" in program_argument_text,
        "working_directory": plist.get("WorkingDirectory") if isinstance(plist.get("WorkingDirectory"), str) else "",
        "runtime_dir": environment.get(ENV_RUNTIME_DIR) if isinstance(environment.get(ENV_RUNTIME_DIR), str) else "",
        "stdout_path": plist.get("StandardOutPath") if isinstance(plist.get("StandardOutPath"), str) else "",
        "stderr_path": plist.get("StandardErrorPath") if isinstance(plist.get("StandardErrorPath"), str) else "",
        "run_at_load": plist.get("RunAtLoad") is True,
        "keep_alive": plist.get("KeepAlive") is True,
    }


def plist_is_valid(metadata: dict[str, Any], *, allow_legacy_daemon: bool = False) -> bool:
    program_daemon = metadata["program_current_daemon"] or (allow_legacy_daemon and metadata["program_legacy_daemon"])
    return bool(
        metadata["label_match"]
        and program_daemon
        and metadata["program_loop"]
        and metadata["program_interval"]
        and metadata["working_directory"]
        and metadata["runtime_dir"]
        and not metadata["run_at_load"]
        and not metadata["keep_alive"]
    )


def plist_bytes(interval_seconds: int) -> bytes:
    return plistlib.dumps(build_launchd_plist(interval_seconds), sort_keys=True)


def launchctl_path() -> str:
    return os.environ.get(ENV_LAUNCHCTL_PATH, DEFAULT_LAUNCHCTL_PATH)


def launchctl_is_fake() -> bool:
    return ENV_LAUNCHCTL_PATH in os.environ


def launchctl_domain() -> str:
    return f"{DOMAIN_PREFIX}/{os.getuid()}"


def launchctl_service_target() -> str:
    return f"{launchctl_domain()}/{AGENT_LABEL}"


def launchctl_supported() -> bool:
    return sys.platform == "darwin" or launchctl_is_fake()


def run_launchctl(args: list[str]) -> subprocess.CompletedProcess[str] | OSError:
    try:
        return subprocess.run([launchctl_path(), *args], text=True, capture_output=True, check=False)
    except OSError as exc:
        return exc


def launchctl_payload(
    mode: str,
    plist_path: Path,
    *,
    action: str,
    target: str,
    invoked: bool,
    status: str,
    returncode: int | None,
    plist_validated: bool,
    error_kind: str | None = None,
) -> dict[str, Any]:
    payload = {
        "timestamp": utc_now(),
        "mode": mode,
        "plist_path": str(plist_path),
        "label": AGENT_LABEL,
        "launchctl_invoked": invoked,
        "launchctl_action": action,
        "launchctl_domain": launchctl_domain(),
        "launchctl_target": target,
        "launchctl_returncode": returncode,
        "launchctl_status": status,
        "plist_validated": plist_validated,
    }
    if error_kind is not None:
        payload["error_kind"] = error_kind
    return payload


def validate_launchctl_plist(plist_path: Path, action: str, target: str) -> tuple[str, int]:
    if not plist_path.exists():
        write_state(
            launchctl_payload(
                f"launchctl-{action}",
                plist_path,
                action=action,
                target=target,
                invoked=False,
                status="missing-plist",
                returncode=None,
                plist_validated=False,
            )
        )
        return "missing-plist", 1

    plist = read_plist(plist_path)
    if plist is None:
        write_state(
            launchctl_payload(
                f"launchctl-{action}",
                plist_path,
                action=action,
                target=target,
                invoked=False,
                status="invalid-plist",
                returncode=None,
                plist_validated=False,
            )
        )
        return "invalid-plist", 1

    metadata = plist_metadata(plist)
    if not plist_is_valid(metadata, allow_legacy_daemon=action in {"status", "bootout"}):
        write_state(
            launchctl_payload(
                f"launchctl-{action}",
                plist_path,
                action=action,
                target=target,
                invoked=False,
                status="refused",
                returncode=None,
                plist_validated=False,
            )
        )
        return "refused", 1
    return "ok", 0


def fixed_launchctl_domain() -> str:
    return f"{FIXED_DOMAIN_PREFIX}/{os.getuid()}"


def fixed_launchctl_service_target() -> str:
    return f"{fixed_launchctl_domain()}/{FIXED_AGENT_LABEL}"


def launchctl_bootout_fixed_label() -> subprocess.CompletedProcess[str] | OSError:
    return run_launchctl(["bootout", fixed_launchctl_service_target()])


def launchctl_print_fixed_label() -> subprocess.CompletedProcess[str] | OSError:
    return run_launchctl(["print", fixed_launchctl_service_target()])


def verified_pid_from_launchctl_output(output: str) -> int | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid = "):
            value = stripped.removeprefix("pid = ").strip()
            try:
                pid = int(value)
            except ValueError:
                return None
            return pid if pid > 0 else None
    return None


def terminate_verified_pid(pid: int) -> bool:
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def launchctl_args(action: str, plist_path: Path) -> tuple[list[str], str]:
    if action == "bootstrap":
        return ["bootstrap", launchctl_domain(), str(plist_path)], launchctl_domain()
    if action == "kickstart":
        return ["kickstart", "-k", launchctl_service_target()], launchctl_service_target()
    if action == "status":
        return ["print", launchctl_service_target()], launchctl_service_target()
    if action == "bootout":
        return ["bootout", launchctl_service_target()], launchctl_service_target()
    raise ValueError(f"unknown launchctl action: {action}")


def launchctl_lifecycle(plist_path: Path, action: str) -> int:
    args, target = launchctl_args(action, plist_path)
    validation_status, validation_exit = validate_launchctl_plist(plist_path, action, target)
    if validation_status != "ok":
        print("\n".join([f"Sidecar daemon launchctl-{action}", f"plist_path: {plist_path}", f"launchctl_action: {action}", f"launchctl_status: {validation_status}", "launchctl_invoked: no"]))
        return validation_exit

    if not launchctl_supported():
        write_state(
            launchctl_payload(
                f"launchctl-{action}",
                plist_path,
                action=action,
                target=target,
                invoked=False,
                status="unsupported-platform",
                returncode=None,
                plist_validated=True,
            )
        )
        print("\n".join([f"Sidecar daemon launchctl-{action}", f"plist_path: {plist_path}", f"launchctl_action: {action}", "launchctl_status: unsupported-platform", "launchctl_invoked: no"]))
        return 1

    result = run_launchctl(args)
    if isinstance(result, OSError):
        write_state(
            launchctl_payload(
                f"launchctl-{action}",
                plist_path,
                action=action,
                target=target,
                invoked=True,
                status="failed",
                returncode=1,
                plist_validated=True,
                error_kind=type(result).__name__,
            )
        )
        print(
            "\n".join(
                [
                    f"Sidecar daemon launchctl-{action}",
                    f"plist_path: {plist_path}",
                    f"launchctl_action: {action}",
                    f"launchctl_target: {target}",
                    "launchctl_invoked: yes",
                    "launchctl_returncode: 1",
                    "launchctl_status: failed",
                    f"error_kind: {type(result).__name__}",
                ]
            )
        )
        return 1

    status = "ok" if result.returncode == 0 else "failed"
    write_state(
        launchctl_payload(
            f"launchctl-{action}",
            plist_path,
            action=action,
            target=target,
            invoked=True,
            status=status,
            returncode=result.returncode,
            plist_validated=True,
        )
    )
    print(
        "\n".join(
            [
                f"Sidecar daemon launchctl-{action}",
                f"plist_path: {plist_path}",
                f"launchctl_action: {action}",
                f"launchctl_target: {target}",
                "launchctl_invoked: yes",
                f"launchctl_returncode: {result.returncode}",
                f"launchctl_status: {status}",
            ]
        )
    )
    return result.returncode


def install_agent_payload(plist_path: Path, interval_seconds: int) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "mode": "install-agent",
        "plist_path": str(plist_path),
        "label": AGENT_LABEL,
        "interval_seconds": interval_seconds,
        "launchctl_invoked": False,
    }


def remove_agent_payload(plist_path: Path, plist_removed: bool, status: str) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "mode": "remove-agent",
        "plist_path": str(plist_path),
        "plist_removed": plist_removed,
        "remove_status": status,
        "launchctl_invoked": False,
    }


def render_plist_metadata(metadata: dict[str, Any]) -> list[str]:
    return [
        f"label={metadata['label']}",
        f"label_match={bool_text(metadata['label_match'])}",
        f"program_daemon={bool_text(metadata['program_daemon'])}",
        f"program_loop={bool_text(metadata['program_loop'])}",
        f"program_interval={bool_text(metadata['program_interval'])}",
        f"working_directory={metadata['working_directory']}",
        f"runtime_dir={metadata['runtime_dir']}",
        f"stdout_path={metadata['stdout_path']}",
        f"stderr_path={metadata['stderr_path']}",
        f"run_at_load={bool_text(metadata['run_at_load'])}",
        f"keep_alive={bool_text(metadata['keep_alive'])}",
    ]


def render_agent_status(plist_path: Path) -> tuple[str, int]:
    lines = ["Sidecar daemon agent-status", f"plist_path: {plist_path}"]
    if not plist_path.exists():
        lines.extend(["plist: absent", "status: absent"])
        return "\n".join(lines), 0

    plist = read_plist(plist_path)
    lines.append("plist: present")
    if plist is None:
        lines.append("status: invalid")
        return "\n".join(lines), 1

    metadata = plist_metadata(plist)
    valid = plist_is_valid(metadata, allow_legacy_daemon=True)
    lines.extend(render_plist_metadata(metadata))
    lines.append(f"status: {'valid' if valid else 'invalid'}")
    return "\n".join(lines), 0 if valid else 1


def render_doctor(plist_path: Path) -> tuple[str, int]:
    lines = ["Sidecar daemon doctor", f"plist_path: {plist_path}"]
    if not plist_path.exists():
        lines.extend(["plist: absent", "plist_valid: no", "launchctl_registered: unknown", "status: absent"])
        return "\n".join(lines), 1

    plist = read_plist(plist_path)
    lines.append("plist: present")
    if plist is None:
        lines.extend(["plist_valid: no", "launchctl_registered: unknown", "status: invalid"])
        return "\n".join(lines), 1

    metadata = plist_metadata(plist)
    valid = plist_is_valid(metadata, allow_legacy_daemon=True)
    lines.extend(render_plist_metadata(metadata))
    lines.append(f"plist_valid: {bool_text(valid)}")
    if not valid:
        lines.extend(["launchctl_registered: unknown", "status: invalid"])
        return "\n".join(lines), 1

    supported = launchctl_supported()
    lines.append(f"launchctl_supported: {bool_text(supported)}")
    if not supported:
        lines.extend(["launchctl_registered: unknown", "status: unsupported"])
        return "\n".join(lines), 1

    target = launchctl_service_target()
    lines.append(f"launchctl_target: {target}")
    result = run_launchctl(["print", target])
    if isinstance(result, OSError):
        lines.extend(["launchctl_registered: unknown", "launchctl_returncode: 1", "status: unknown", f"error_kind: {type(result).__name__}"])
        return "\n".join(lines), 1

    lines.append(f"launchctl_returncode: {result.returncode}")
    registered = result.returncode == 0
    lines.append(f"launchctl_registered: {bool_text(registered)}")
    lines.append(f"status: {'ok' if registered else 'not-registered'}")
    return "\n".join(lines), 0 if registered else 1


def write_plist_file(plist_path: Path, content: bytes) -> None:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    fd: int | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{plist_path.name}.", suffix=".tmp", dir=plist_path.parent)
        temp_path = Path(temp_name)
        os.fchmod(fd, PLIST_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(content)
        temp_path.replace(plist_path)
        plist_path.chmod(PLIST_FILE_MODE)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def install_agent(plist_path: Path, interval_seconds: int) -> int:
    content = plist_bytes(interval_seconds)
    write_plist_file(plist_path, content)
    write_state(install_agent_payload(plist_path, interval_seconds))
    print(f"Wrote launchd plist to {plist_path}")
    print("launchctl was not invoked")
    return 0


def agent_status(plist_path: Path) -> int:
    output, exit_code = render_agent_status(plist_path)
    print(output)
    return exit_code


def doctor(plist_path: Path) -> int:
    output, exit_code = render_doctor(plist_path)
    print(output)
    return exit_code


def remove_agent(plist_path: Path) -> int:
    lines = ["Sidecar daemon remove-agent", f"plist_path: {plist_path}"]
    if not plist_path.exists():
        write_state(remove_agent_payload(plist_path, False, "absent"))
        lines.extend(["plist: absent", "plist_removed: no", "launchctl_invoked: no", "status: absent"])
        print("\n".join(lines))
        return 0

    plist = read_plist(plist_path)
    lines.append("plist: present")
    if plist is None:
        write_state(remove_agent_payload(plist_path, False, "invalid"))
        lines.extend(["plist_removed: no", "launchctl_invoked: no", "status: invalid"])
        print("\n".join(lines))
        return 1

    metadata = plist_metadata(plist)
    if not plist_is_valid(metadata, allow_legacy_daemon=True):
        write_state(remove_agent_payload(plist_path, False, "refused"))
        lines.extend(["plist_removed: no", "launchctl_invoked: no", "status: refused"])
        print("\n".join(lines))
        return 1

    plist_path.unlink()
    write_state(remove_agent_payload(plist_path, True, "removed"))
    lines.extend(["plist_removed: yes", "launchctl_invoked: no", "status: removed"])
    print("\n".join(lines))
    return 0


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("interval-seconds must be positive")
    return parsed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local sidecar daemon maintenance.")
    parser.add_argument("--config", help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-once", action="store_true", help="Run one local maintenance pass and exit.")
    mode.add_argument("--loop", action="store_true", help="Run repeated local maintenance passes in the foreground.")
    mode.add_argument("--install-agent", action="store_true", help="Write a launchd user-agent plist without starting it.")
    mode.add_argument("--agent-status", action="store_true", help="Inspect an explicit launchd plist artifact without starting it.")
    mode.add_argument("--doctor", action="store_true", help="Read-only launchd and plist diagnostics for an explicit sidecar plist.")
    mode.add_argument("--remove-agent", action="store_true", help="Remove an explicit sidecar launchd plist artifact without unloading it.")
    mode.add_argument("--launchctl-bootstrap", action="store_true", help="Explicitly register the plist with launchd.")
    mode.add_argument("--launchctl-kickstart", action="store_true", help="Explicitly kickstart the launchd service.")
    mode.add_argument("--launchctl-status", action="store_true", help="Explicitly query launchd service status.")
    mode.add_argument("--launchctl-bootout", action="store_true", help="Explicitly unregister the launchd service.")
    parser.add_argument("--interval-seconds", type=positive_int, default=DEFAULT_INTERVAL_SECONDS, help="Loop interval in seconds.")
    parser.add_argument("--max-runs", type=positive_int, help="Maximum loop runs before exiting; intended for tests.")
    parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl modes run when explicitly selected.")
    parser.add_argument("--plist-path", type=Path, help="Explicit path for the launchd plist; required for plist artifact and launchctl modes.")
    parser.add_argument("--operation-log", action="store_true", default=DEFAULT_OPERATION_LOG, help="append metadata-only daemon operations to operation-log.jsonl")
    parser.add_argument("--no-operation-log", action="store_false", dest="operation_log", help="disable operation logging even if config enables it")
    args = parser.parse_args(argv)
    if args.install_agent and args.plist_path is None:
        parser.error("--plist-path is required")
    launchctl_requested = args.launchctl_bootstrap or args.launchctl_kickstart or args.launchctl_status or args.launchctl_bootout
    if (args.agent_status or args.doctor or args.remove_agent or launchctl_requested) and args.plist_path is None:
        parser.error("--plist-path is required")
    return args


def main(argv: list[str] | None = None) -> int:
    active_argv = sys.argv[1:] if argv is None else argv
    config_path = cli_config_path(active_argv)
    active_config_path = config_path or os.environ.get(CONFIG_PATH_ENV, "").strip() or None
    if config_path:
        os.environ[CONFIG_PATH_ENV] = config_path
    try:
        refresh_config(active_config_path)
    except SidecarConfigError as exc:
        print_config_error("compact_sidecar.services.daemon", exc)
        return 1
    args = parse_args(active_argv)
    if args.run_once:
        exit_code = run_once()
        log_llm_summary_operation(args.operation_log)
        log_daemon_operation(args.operation_log, "run-once", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.loop:
        exit_code = run_loop(args.interval_seconds, args.max_runs, args.operation_log)
        log_daemon_operation(args.operation_log, "loop", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code

    plist_path = args.plist_path.expanduser() if args.plist_path is not None else None
    if plist_path is None:
        print("internal error: missing plist path", file=sys.stderr)
        return 2
    if args.agent_status:
        exit_code = agent_status(plist_path)
        log_daemon_operation(args.operation_log, "agent-status", "ok" if exit_code == 0 else "error", {"plist_path": str(plist_path), "exit_code": exit_code})
        return exit_code
    if args.doctor:
        resolved = plist_path.resolve()
        exit_code = doctor(resolved)
        log_daemon_operation(args.operation_log, "doctor", "ok" if exit_code == 0 else "error", {"plist_path": str(resolved), "exit_code": exit_code})
        return exit_code
    if args.remove_agent:
        exit_code = remove_agent(plist_path)
        log_daemon_operation(args.operation_log, "remove-agent", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.launchctl_bootstrap:
        resolved = plist_path.resolve()
        exit_code = launchctl_lifecycle(resolved, "bootstrap")
        log_daemon_operation(args.operation_log, "launchctl-bootstrap", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.launchctl_kickstart:
        resolved = plist_path.resolve()
        exit_code = launchctl_lifecycle(resolved, "kickstart")
        log_daemon_operation(args.operation_log, "launchctl-kickstart", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.launchctl_status:
        resolved = plist_path.resolve()
        exit_code = launchctl_lifecycle(resolved, "status")
        log_daemon_operation(args.operation_log, "launchctl-status", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.launchctl_bootout:
        resolved = plist_path.resolve()
        exit_code = launchctl_lifecycle(resolved, "bootout")
        log_daemon_operation(args.operation_log, "launchctl-bootout", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code

    try:
        exit_code = install_agent(plist_path, args.interval_seconds)
    except SidecarConfigError as exc:
        print_config_error("compact_sidecar.services.daemon", exc)
        return 1
    log_daemon_operation(args.operation_log, "install-agent", "ok" if exit_code == 0 else "error", read_state_metadata())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
