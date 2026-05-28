from __future__ import annotations

import argparse
import json
import plistlib
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_candidates import collect_recent_candidates
from operation_log import append_operation
from merge_compact_history import DRAFT_NAME, MAX_DRAFT_SUMMARIES, build_draft
from sidecar_paths import ENV_RUNTIME_DIR, project_root, runtime_dir, runtime_path

STATE_NAME = "daemon-state.json"
AGENT_LABEL = "com.claude-code-compact-sidecar.daemon"
DEFAULT_INTERVAL_SECONDS = 300
ENV_LAUNCHCTL_PATH = "SIDECAR_LAUNCHCTL_PATH"


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


def run_once_payload(candidate_count: int, draft_path: Path) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "mode": "run-once",
        "candidate_count": candidate_count,
        "draft_path": str(draft_path),
        "draft_written": True,
    }


def write_draft_from_history() -> tuple[int, Path]:
    candidates = collect_recent_candidates(limit=MAX_DRAFT_SUMMARIES, service="daemon")
    draft_path = runtime_path(DRAFT_NAME)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(build_draft(candidates), encoding="utf-8")
    return len(candidates), draft_path


def run_once() -> int:
    candidate_count, draft_path = write_draft_from_history()
    write_state(run_once_payload(candidate_count, draft_path))

    lines = [
        "Sidecar daemon run-once",
        f"runtime_dir: {runtime_dir()}",
        f"candidate_count: {candidate_count}",
        f"draft_path: {draft_path}",
        "rolling-summary.md: not modified",
    ]
    print("\n".join(lines))
    return 0


def loop_payload(candidate_count: int, draft_path: Path, interval_seconds: int, run_count: int, shutdown_reason: str) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "mode": "loop",
        "candidate_count": candidate_count,
        "draft_path": str(draft_path),
        "draft_written": True,
        "interval_seconds": interval_seconds,
        "run_count": run_count,
        "shutdown_reason": shutdown_reason,
    }


def run_loop(interval_seconds: int, max_runs: int | None) -> int:
    run_count = 0
    last_candidate_count = 0
    last_draft_path = runtime_path(DRAFT_NAME)
    shutdown_reason = "interrupted"

    try:
        while max_runs is None or run_count < max_runs:
            last_candidate_count, last_draft_path = write_draft_from_history()
            run_count += 1
            write_state(loop_payload(last_candidate_count, last_draft_path, interval_seconds, run_count, "running"))
            if max_runs is not None and run_count >= max_runs:
                shutdown_reason = "max-runs"
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        shutdown_reason = "interrupted"

    write_state(loop_payload(last_candidate_count, last_draft_path, interval_seconds, run_count, shutdown_reason))
    print("\n".join(["Sidecar daemon loop", f"runtime_dir: {runtime_dir()}", f"run_count: {run_count}", f"shutdown_reason: {shutdown_reason}"]))
    return 0


def daemon_script_path() -> Path:
    return Path(__file__).resolve()


def build_launchd_plist(interval_seconds: int) -> dict[str, Any]:
    runtime = runtime_dir()
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": [
            "python3",
            str(daemon_script_path()),
            "--loop",
            "--interval-seconds",
            str(interval_seconds),
        ],
        "WorkingDirectory": str(project_root(daemon_script_path().parent)),
        "EnvironmentVariables": {ENV_RUNTIME_DIR: str(runtime)},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(runtime / "daemon.out.log"),
        "StandardErrorPath": str(runtime / "daemon.err.log"),
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
        "program_daemon": any(Path(part).name == "daemon.py" for part in program_argument_text),
        "program_loop": "--loop" in program_argument_text,
        "program_interval": "--interval-seconds" in program_argument_text,
        "working_directory": plist.get("WorkingDirectory") if isinstance(plist.get("WorkingDirectory"), str) else "",
        "runtime_dir": environment.get(ENV_RUNTIME_DIR) if isinstance(environment.get(ENV_RUNTIME_DIR), str) else "",
        "stdout_path": plist.get("StandardOutPath") if isinstance(plist.get("StandardOutPath"), str) else "",
        "stderr_path": plist.get("StandardErrorPath") if isinstance(plist.get("StandardErrorPath"), str) else "",
        "run_at_load": plist.get("RunAtLoad") is True,
        "keep_alive": plist.get("KeepAlive") is True,
    }


def plist_is_valid(metadata: dict[str, Any]) -> bool:
    return bool(
        metadata["label_match"]
        and metadata["program_daemon"]
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
    return os.environ.get(ENV_LAUNCHCTL_PATH, "launchctl")


def launchctl_is_fake() -> bool:
    return ENV_LAUNCHCTL_PATH in os.environ


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


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
    if not plist_is_valid(metadata):
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
    valid = plist_is_valid(metadata)
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
    valid = plist_is_valid(metadata)
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


def install_agent(plist_path: Path, interval_seconds: int) -> int:
    content = plist_bytes(interval_seconds)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(content)
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
    if not plist_is_valid(metadata):
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
    parser.add_argument("--operation-log", action="store_true", help="append metadata-only daemon operations to operation-log.jsonl")
    args = parser.parse_args(argv)
    if args.install_agent and args.plist_path is None:
        parser.error("--plist-path is required")
    launchctl_requested = args.launchctl_bootstrap or args.launchctl_kickstart or args.launchctl_status or args.launchctl_bootout
    if (args.agent_status or args.doctor or args.remove_agent or launchctl_requested) and args.plist_path is None:
        parser.error("--plist-path is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.run_once:
        exit_code = run_once()
        log_daemon_operation(args.operation_log, "run-once", "ok" if exit_code == 0 else "error", read_state_metadata())
        return exit_code
    if args.loop:
        exit_code = run_loop(args.interval_seconds, args.max_runs)
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

    exit_code = install_agent(plist_path, args.interval_seconds)
    log_daemon_operation(args.operation_log, "install-agent", "ok" if exit_code == 0 else "error", read_state_metadata())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
