from __future__ import annotations

import argparse
import json
import plistlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_candidates import collect_recent_candidates
from merge_compact_history import DRAFT_NAME, MAX_DRAFT_SUMMARIES, build_draft
from sidecar_paths import ENV_RUNTIME_DIR, project_root, runtime_dir, runtime_path

STATE_NAME = "daemon-state.json"
AGENT_LABEL = "com.claude-code-compact-sidecar.daemon"
DEFAULT_INTERVAL_SECONDS = 300


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(payload: dict[str, Any]) -> None:
    state_path = runtime_path(STATE_NAME)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def plist_bytes(interval_seconds: int) -> bytes:
    return plistlib.dumps(build_launchd_plist(interval_seconds), sort_keys=True)


def install_agent(plist_path: Path, interval_seconds: int, *, dry_run: bool) -> int:
    content = plist_bytes(interval_seconds)
    if dry_run:
        sys.stdout.write(content.decode("utf-8"))
        return 0

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(content)
    print(f"Wrote launchd plist to {plist_path}")
    print("launchctl was not invoked")
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
    mode.add_argument("--install-agent", action="store_true", help="Write or preview a launchd user-agent plist without starting it.")
    parser.add_argument("--interval-seconds", type=positive_int, default=DEFAULT_INTERVAL_SECONDS, help="Loop interval in seconds.")
    parser.add_argument("--max-runs", type=positive_int, help="Maximum loop runs before exiting; intended for tests.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated launchd plist without writing it.")
    parser.add_argument("--plist-path", type=Path, help="Explicit path for the launchd plist; required unless --dry-run is set.")
    args = parser.parse_args(argv)
    if args.install_agent and not args.dry_run and args.plist_path is None:
        parser.error("--plist-path is required unless --dry-run is set")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.run_once:
        return run_once()
    if args.loop:
        return run_loop(args.interval_seconds, args.max_runs)
    plist_path = args.plist_path.expanduser() if args.plist_path is not None else runtime_dir() / "launchd-dry-run.plist"
    return install_agent(plist_path, args.interval_seconds, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
