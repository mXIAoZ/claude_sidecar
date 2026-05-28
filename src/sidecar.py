from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import sys
from pathlib import Path

import auto_compact_controller
import daemon
import dashboard
import install_hooks


def add_compact_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pane", help="explicit tmux target pane, for example session:window.pane")
    parser.add_argument("--confirm-send", action="store_true", help="compatibility no-op; tmux sends are enabled by default")
    parser.add_argument("--no-send", action="store_true", help="print the plan without sending tmux keys")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt-file", type=Path, help="explicit file containing the prompt to send after compact")
    prompt_group.add_argument("--prompt-stdin", action="store_true", help="read the prompt to send from stdin")
    parser.add_argument("--min-readiness", choices=tuple(auto_compact_controller.READINESS_ORDER), default="high")
    parser.add_argument("--wait-postcompact", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=auto_compact_controller.positive_float, default=auto_compact_controller.DEFAULT_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=auto_compact_controller.positive_float, default=auto_compact_controller.DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--merge-after", action="store_true")
    parser.add_argument("--tmux-path", default="tmux")
    parser.add_argument("--operation-log", action="store_true", help="append metadata-only controller operations to operation-log.jsonl")
    parser.add_argument("--log-raw-prompt", action="store_true", help="store bounded raw prompt text in operation-log.jsonl; sensitive")


def compact_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.pane:
        argv.extend(["--pane", args.pane])
    if args.confirm_send:
        argv.append("--confirm-send")
    if args.no_send:
        argv.append("--no-send")
    if args.prompt_file is not None:
        argv.extend(["--prompt-file", str(args.prompt_file)])
    if args.prompt_stdin:
        argv.append("--prompt-stdin")
    argv.extend(["--min-readiness", args.min_readiness])
    if args.wait_postcompact:
        argv.append("--wait-postcompact")
    argv.extend(["--wait-timeout-seconds", str(args.wait_timeout_seconds)])
    argv.extend(["--poll-interval-seconds", str(args.poll_interval_seconds)])
    if args.merge_after:
        argv.append("--merge-after")
    argv.extend(["--tmux-path", args.tmux_path])
    if args.operation_log:
        argv.append("--operation-log")
    if args.log_raw_prompt:
        argv.append("--log-raw-prompt")
    return argv


def hook_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.settings is not None:
        argv.extend(["--settings", str(args.settings)])
    if args.confirm_user_settings:
        argv.append("--confirm-user-settings")
    return argv


def run_daemon_main(argv: list[str]) -> int:
    return daemon.main(argv)


def run_hooks(args: argparse.Namespace) -> int:
    return install_hooks.main(hook_argv(args))


def setup(args: argparse.Namespace) -> int:
    if args.start_daemon and args.plist_path is None:
        print("--plist-path is required with --start-daemon", file=sys.stderr)
        return 2

    hook_exit = run_hooks(args)
    if hook_exit != 0:
        return hook_exit

    if args.plist_path is not None:
        install_exit = run_daemon_main(["--install-agent", "--plist-path", str(args.plist_path), "--interval-seconds", str(args.interval_seconds)])
        if install_exit != 0:
            return install_exit

    if args.start_daemon:
        if args.no_launchctl:
            print("launchctl_disabled=yes")
        else:
            for mode in ("--launchctl-bootstrap", "--launchctl-kickstart", "--launchctl-status"):
                exit_code = run_daemon_main([mode, "--plist-path", str(args.plist_path)])
                if exit_code != 0:
                    return exit_code

    has_prompt = args.prompt_file is not None or args.prompt_stdin
    if has_prompt:
        return auto_compact_controller.main(compact_argv(args))
    if args.pane:
        command = f"python3 src/sidecar.py start compact --pane {shlex.quote(args.pane)} --prompt-file /path/to/prompt.txt"
        print("Auto compact controller is ready for explicit prompt sends.")
        print(f"next_command: {command}")
    return 0


def start_daemon(args: argparse.Namespace) -> int:
    if args.plist_path is None:
        print("--plist-path is required", file=sys.stderr)
        return 2
    install_exit = run_daemon_main(["--install-agent", "--plist-path", str(args.plist_path), "--interval-seconds", str(args.interval_seconds)])
    if install_exit != 0:
        return install_exit
    if args.no_launchctl:
        print("launchctl_disabled=yes")
        return 0
    for mode in ("--launchctl-bootstrap", "--launchctl-kickstart", "--launchctl-status"):
        exit_code = run_daemon_main([mode, "--plist-path", str(args.plist_path)])
        if exit_code != 0:
            return exit_code
    return 0


def capture_stdout(func: object, *args: object) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = func(*args)  # type: ignore[misc]
    return int(exit_code), buffer.getvalue()


def render_status(args: argparse.Namespace) -> int:
    if args.doctor and args.plist_path is None:
        print("--plist-path is required with --doctor", file=sys.stderr)
        return 2

    snapshot = dashboard.build_dashboard_snapshot(log_limit=args.log_limit)
    if args.json:
        output_snapshot = dashboard.snapshot_for_output(snapshot, show_content=args.show_content)
        if args.plist_path is not None:
            exit_code, output = capture_stdout(daemon.agent_status, args.plist_path.expanduser())
            output_snapshot["agent_status"] = {"exit_code": exit_code, "text": output}
        if args.doctor:
            exit_code, output = capture_stdout(daemon.doctor, args.plist_path.expanduser().resolve())
            output_snapshot["doctor"] = {"exit_code": exit_code, "text": output}
        print(json.dumps(output_snapshot, ensure_ascii=False, indent=2))
        return int(output_snapshot.get("doctor", {}).get("exit_code", 0))
    print(dashboard.render_dashboard(snapshot, color=not args.no_color, show_content=args.show_content))
    if args.plist_path is not None:
        print()
        daemon.agent_status(args.plist_path.expanduser())
    if args.doctor:
        return daemon.doctor(args.plist_path.expanduser().resolve())
    return 0


def add_hook_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--settings", type=Path, help="Path to Claude Code settings.json. Defaults to ~/.claude/settings.json.")
    parser.add_argument("--confirm-user-settings", action="store_true", help="Compatibility no-op; default ~/.claude/settings.json writes are allowed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Claude Code compact sidecar CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Configure hooks, daemon, and optional auto compact in one command.")
    add_hook_arguments(setup_parser)
    setup_parser.add_argument("--plist-path", type=Path, help="Explicit launchd plist path to write and optionally start.")
    setup_parser.add_argument("--interval-seconds", type=daemon.positive_int, default=daemon.DEFAULT_INTERVAL_SECONDS)
    setup_parser.add_argument("--start-daemon", action="store_true", help="Bootstrap, kickstart, and query launchd after writing the plist.")
    setup_parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl startup is enabled by default.")
    setup_parser.add_argument("--no-launchctl", action="store_true", help="Write setup files without invoking launchctl.")
    add_compact_arguments(setup_parser)
    setup_parser.set_defaults(func=setup)

    start_parser = subparsers.add_parser("start", help="Start sidecar daemon or compact controller.")
    start_subparsers = start_parser.add_subparsers(dest="start_command", required=True)
    start_daemon_parser = start_subparsers.add_parser("daemon", help="Install and start the launchd daemon.")
    start_daemon_parser.add_argument("--plist-path", type=Path, required=True, help="Explicit launchd plist path.")
    start_daemon_parser.add_argument("--interval-seconds", type=daemon.positive_int, default=daemon.DEFAULT_INTERVAL_SECONDS)
    start_daemon_parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl startup is enabled by default.")
    start_daemon_parser.add_argument("--no-launchctl", action="store_true", help="Install the plist without invoking launchctl.")
    start_daemon_parser.set_defaults(func=start_daemon)
    start_compact_parser = start_subparsers.add_parser("compact", help="Run explicit tmux auto compact controller.")
    add_compact_arguments(start_compact_parser)
    start_compact_parser.set_defaults(func=lambda args: auto_compact_controller.main(compact_argv(args)))

    status_parser = subparsers.add_parser("status", help="Show read-only sidecar status.")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--show-content", action="store_true", help="Display raw logged prompt/summary content; sensitive.")
    status_parser.add_argument("--log-limit", type=dashboard.positive_int, default=dashboard.DEFAULT_LOG_LIMIT)
    status_parser.add_argument("--no-color", action="store_true")
    status_parser.add_argument("--plist-path", type=Path, help="Include explicit launchd plist artifact status.")
    status_parser.add_argument("--doctor", action="store_true", help="Run read-only launchd doctor for --plist-path.")
    status_parser.set_defaults(func=render_status)

    compact_parser = subparsers.add_parser("compact", help="Alias for start compact.")
    add_compact_arguments(compact_parser)
    compact_parser.set_defaults(func=lambda args: auto_compact_controller.main(compact_argv(args)))

    hooks_parser = subparsers.add_parser("hooks", help="Install Claude Code hooks.")
    add_hook_arguments(hooks_parser)
    hooks_parser.set_defaults(func=run_hooks)

    daemon_parser = subparsers.add_parser("daemon", help="Run existing daemon CLI modes.")
    daemon_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to daemon.py, prefix with -- after daemon.")
    daemon_parser.set_defaults(func=lambda args: daemon.main(args.args))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
