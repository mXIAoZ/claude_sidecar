from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import os
import sys
from pathlib import Path

import auto_compact_controller
import daemon
import dashboard
import install_hooks
from sidecar_config import CONFIG_PATH_ENV, SidecarConfigError, cli_config_path, config_path_env, load_config_for_import, load_config_safe, print_config_error

_CONFIG = load_config_for_import()
_CONTROLLER_CONFIG = _CONFIG["controller"]
_DAEMON_CONFIG = _CONFIG["daemon_launchd"]
_DASHBOARD_CONFIG = _CONFIG["dashboard_status"]


def refresh_config(config_path: str | None = None) -> None:
    global _CONFIG, _CONTROLLER_CONFIG, _DAEMON_CONFIG, _DASHBOARD_CONFIG
    _CONFIG = load_config_safe(config_path)
    _CONTROLLER_CONFIG = _CONFIG["controller"]
    _DAEMON_CONFIG = _CONFIG["daemon_launchd"]
    _DASHBOARD_CONFIG = _CONFIG["dashboard_status"]


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="Path to sidecar config JSON. Defaults to SIDECAR_CONFIG_PATH or the built-in template.",
    )


def config_argv(args: argparse.Namespace) -> list[str]:
    return ["--config", str(args.config)] if getattr(args, "config", None) else []


def config_env_argv() -> list[str]:
    env = config_path_env(_CONFIG)
    return ["--config", env[CONFIG_PATH_ENV]] if env else []


def active_config_argv(args: argparse.Namespace) -> list[str]:
    explicit = config_argv(args)
    return explicit if explicit else config_env_argv()


def add_compact_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pane", default=str(_CONTROLLER_CONFIG.get("pane") or "") or None, help="explicit tmux target pane, for example session:window.pane")
    parser.add_argument("--confirm-send", action="store_true", default=bool(_CONTROLLER_CONFIG.get("confirm_send")), help="compatibility no-op; tmux sends are enabled by default")
    parser.add_argument("--no-send", action="store_true", default=bool(_CONTROLLER_CONFIG["no_send"]), help="print the plan without sending tmux keys")
    parser.add_argument("--send", action="store_false", dest="no_send", help="send tmux keys even if config enables no_send")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt-file", type=Path, help="explicit file containing the prompt to send after compact")
    prompt_group.add_argument("--prompt-stdin", action="store_true", help="read the prompt to send from stdin")
    parser.add_argument("--min-readiness", choices=tuple(auto_compact_controller.READINESS_ORDER), default=str(_CONTROLLER_CONFIG["min_readiness"]))
    parser.add_argument("--wait-postcompact", action="store_true", default=bool(_CONTROLLER_CONFIG["wait_postcompact"]))
    parser.add_argument("--no-wait-postcompact", action="store_false", dest="wait_postcompact", help="disable postcompact waiting even if config enables it")
    parser.add_argument("--wait-timeout-seconds", type=auto_compact_controller.positive_float, default=float(_CONTROLLER_CONFIG["wait_timeout_seconds"]))
    parser.add_argument("--poll-interval-seconds", type=auto_compact_controller.positive_float, default=float(_CONTROLLER_CONFIG["poll_interval_seconds"]))
    parser.add_argument("--merge-after", action="store_true", default=bool(_CONTROLLER_CONFIG["merge_after"]))
    parser.add_argument("--no-merge-after", action="store_false", dest="merge_after", help="disable merge-after even if config enables it")
    parser.add_argument("--tmux-path", default=str(_CONTROLLER_CONFIG["tmux_path"]))
    parser.add_argument("--operation-log", action="store_true", default=bool(_CONTROLLER_CONFIG["operation_log"]), help="append metadata-only controller operations to operation-log.jsonl")
    parser.add_argument("--no-operation-log", action="store_false", dest="operation_log", help="disable operation logging even if config enables it")
    parser.add_argument("--log-raw-prompt", action="store_true", default=bool(_CONTROLLER_CONFIG["log_raw_prompt"]), help="store bounded raw prompt text in operation-log.jsonl; sensitive")
    parser.add_argument("--no-log-raw-prompt", action="store_false", dest="log_raw_prompt", help="disable raw prompt logging even if config enables it")


def compact_bool_argv(args: argparse.Namespace, attr: str, default: bool, positive: str, negative: str | None = None) -> list[str]:
    value = bool(getattr(args, attr))
    if value:
        return [positive]
    if default and negative is not None:
        return [negative]
    return []


def compact_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = active_config_argv(args)
    if args.pane:
        argv.extend(["--pane", args.pane])
    if args.confirm_send:
        argv.append("--confirm-send")
    argv.extend(compact_bool_argv(args, "no_send", bool(_CONTROLLER_CONFIG["no_send"]), "--no-send", "--send"))
    if args.prompt_file is not None:
        argv.extend(["--prompt-file", str(args.prompt_file)])
    if args.prompt_stdin:
        argv.append("--prompt-stdin")
    argv.extend(["--min-readiness", args.min_readiness])
    argv.extend(compact_bool_argv(args, "wait_postcompact", bool(_CONTROLLER_CONFIG["wait_postcompact"]), "--wait-postcompact", "--no-wait-postcompact"))
    argv.extend(["--wait-timeout-seconds", str(args.wait_timeout_seconds)])
    argv.extend(["--poll-interval-seconds", str(args.poll_interval_seconds)])
    argv.extend(compact_bool_argv(args, "merge_after", bool(_CONTROLLER_CONFIG["merge_after"]), "--merge-after", "--no-merge-after"))
    argv.extend(["--tmux-path", args.tmux_path])
    argv.extend(compact_bool_argv(args, "operation_log", bool(_CONTROLLER_CONFIG["operation_log"]), "--operation-log", "--no-operation-log"))
    argv.extend(compact_bool_argv(args, "log_raw_prompt", bool(_CONTROLLER_CONFIG["log_raw_prompt"]), "--log-raw-prompt", "--no-log-raw-prompt"))
    return argv


def hook_argv(args: argparse.Namespace, *, uninstall: bool = False) -> list[str]:
    argv: list[str] = active_config_argv(args)
    if args.settings is not None:
        argv.extend(["--settings", str(args.settings)])
    if args.confirm_user_settings:
        argv.append("--confirm-user-settings")
    if uninstall:
        argv.append("--uninstall")
    return argv


def daemon_argv(args: argparse.Namespace, *daemon_args: str) -> list[str]:
    return [*active_config_argv(args), *daemon_args]


def run_daemon_main(argv: list[str]) -> int:
    return daemon.main(argv)


def run_hooks(args: argparse.Namespace, *, uninstall: bool = False) -> int:
    return install_hooks.main(hook_argv(args, uninstall=uninstall))


def uninstall(args: argparse.Namespace) -> int:
    if args.remove_daemon and args.plist_path is None:
        print("--plist-path is required with --remove-daemon", file=sys.stderr)
        return 2

    exit_code = 0
    if not args.keep_hooks:
        exit_code = run_hooks(args, uninstall=True)
        if exit_code != 0:
            return exit_code

    if args.remove_daemon:
        if args.no_launchctl:
            print("launchctl_disabled=yes")
        else:
            bootout_exit = run_daemon_main(daemon_argv(args, "--launchctl-bootout", "--plist-path", str(args.plist_path)))
            if bootout_exit != 0 and not args.ignore_bootout_failure:
                return bootout_exit
        remove_exit = run_daemon_main(daemon_argv(args, "--remove-agent", "--plist-path", str(args.plist_path)))
        if remove_exit != 0:
            return remove_exit

    return exit_code


def setup(args: argparse.Namespace) -> int:
    if args.start_daemon and args.plist_path is None:
        print("--plist-path is required with --start-daemon", file=sys.stderr)
        return 2

    hook_exit = run_hooks(args)
    if hook_exit != 0:
        return hook_exit

    if args.plist_path is not None:
        install_exit = run_daemon_main(daemon_argv(args, "--install-agent", "--plist-path", str(args.plist_path), "--interval-seconds", str(args.interval_seconds)))
        if install_exit != 0:
            return install_exit

    if args.start_daemon:
        if args.no_launchctl:
            print("launchctl_disabled=yes")
        else:
            for mode in ("--launchctl-bootstrap", "--launchctl-kickstart", "--launchctl-status"):
                exit_code = run_daemon_main(daemon_argv(args, mode, "--plist-path", str(args.plist_path)))
                if exit_code != 0:
                    return exit_code

    has_prompt = args.prompt_file is not None or args.prompt_stdin
    if has_prompt:
        return auto_compact_controller.main(compact_argv(args))
    if args.pane:
        command = shlex.join([str(_CONFIG['paths']['python_executable']), "src/sidecar.py", "start", "compact", "--pane", args.pane, "--prompt-file", "/path/to/prompt.txt"])
        print("Auto compact controller is ready for explicit prompt sends.")
        print(f"next_command: {command}")
    return 0


def start_daemon(args: argparse.Namespace) -> int:
    if args.plist_path is None:
        print("--plist-path is required", file=sys.stderr)
        return 2
    install_exit = run_daemon_main(daemon_argv(args, "--install-agent", "--plist-path", str(args.plist_path), "--interval-seconds", str(args.interval_seconds)))
    if install_exit != 0:
        return install_exit
    if args.no_launchctl:
        print("launchctl_disabled=yes")
        return 0
    for mode in ("--launchctl-bootstrap", "--launchctl-kickstart", "--launchctl-status"):
        exit_code = run_daemon_main(daemon_argv(args, mode, "--plist-path", str(args.plist_path)))
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
    add_config_argument(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Configure hooks, daemon, and optional auto compact in one command.")
    add_config_argument(setup_parser)
    add_hook_arguments(setup_parser)
    setup_parser.add_argument("--plist-path", type=Path, help="Explicit launchd plist path to write and optionally start.")
    setup_parser.add_argument("--interval-seconds", type=daemon.positive_int, default=int(_DAEMON_CONFIG["default_interval_seconds"]))
    setup_parser.add_argument("--start-daemon", action="store_true", help="Bootstrap, kickstart, and query launchd after writing the plist.")
    setup_parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl startup is enabled by default.")
    setup_parser.add_argument("--no-launchctl", action="store_true", help="Write setup files without invoking launchctl.")
    add_compact_arguments(setup_parser)
    setup_parser.set_defaults(func=setup)

    start_parser = subparsers.add_parser("start", help="Start sidecar daemon or compact controller.")
    add_config_argument(start_parser)
    start_subparsers = start_parser.add_subparsers(dest="start_command", required=True)
    start_daemon_parser = start_subparsers.add_parser("daemon", help="Install and start the launchd daemon.")
    add_config_argument(start_daemon_parser)
    start_daemon_parser.add_argument("--plist-path", type=Path, required=True, help="Explicit launchd plist path.")
    start_daemon_parser.add_argument("--interval-seconds", type=daemon.positive_int, default=int(_DAEMON_CONFIG["default_interval_seconds"]))
    start_daemon_parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl startup is enabled by default.")
    start_daemon_parser.add_argument("--no-launchctl", action="store_true", help="Install the plist without invoking launchctl.")
    start_daemon_parser.set_defaults(func=start_daemon)
    start_compact_parser = start_subparsers.add_parser("compact", help="Run explicit tmux auto compact controller.")
    add_config_argument(start_compact_parser)
    add_compact_arguments(start_compact_parser)
    start_compact_parser.set_defaults(func=lambda args: auto_compact_controller.main(compact_argv(args)))

    status_parser = subparsers.add_parser("status", help="Show read-only sidecar status.")
    add_config_argument(status_parser)
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--show-content", action="store_true", help="Display raw logged prompt/summary content; sensitive.")
    status_parser.add_argument("--log-limit", type=dashboard.positive_int, default=int(_DASHBOARD_CONFIG["log_limit"]))
    status_parser.add_argument("--no-color", action="store_true")
    status_parser.add_argument("--plist-path", type=Path, help="Include explicit launchd plist artifact status.")
    status_parser.add_argument("--doctor", action="store_true", help="Run read-only launchd doctor for --plist-path.")
    status_parser.set_defaults(func=render_status)

    compact_parser = subparsers.add_parser("compact", help="Alias for start compact.")
    add_config_argument(compact_parser)
    add_compact_arguments(compact_parser)
    compact_parser.set_defaults(func=lambda args: auto_compact_controller.main(compact_argv(args)))

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove sidecar hooks and optionally stop/remove the daemon.")
    add_config_argument(uninstall_parser)
    add_hook_arguments(uninstall_parser)
    uninstall_parser.add_argument("--keep-hooks", action="store_true", help="Do not remove sidecar hook entries from settings.")
    uninstall_parser.add_argument("--remove-daemon", action="store_true", help="Boot out launchd service and remove the sidecar plist.")
    uninstall_parser.add_argument("--plist-path", type=Path, help="Explicit launchd plist path to boot out and remove.")
    uninstall_parser.add_argument("--confirm-launchctl", action="store_true", help="Compatibility no-op; launchctl bootout is enabled by default.")
    uninstall_parser.add_argument("--no-launchctl", action="store_true", help="Remove the plist without invoking launchctl bootout.")
    uninstall_parser.add_argument("--ignore-bootout-failure", action="store_true", help="Continue removing the plist if launchctl bootout fails.")
    uninstall_parser.set_defaults(func=uninstall)

    hooks_parser = subparsers.add_parser("hooks", help="Install Claude Code hooks.")
    add_config_argument(hooks_parser)
    add_hook_arguments(hooks_parser)
    hooks_parser.set_defaults(func=run_hooks)

    daemon_parser = subparsers.add_parser("daemon", help="Run existing daemon CLI modes.")
    add_config_argument(daemon_parser)
    daemon_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to daemon.py, prefix with -- after daemon.")
    daemon_parser.set_defaults(func=lambda args: daemon.main([*active_config_argv(args), *args.args]))
    return parser


def main(argv: list[str] | None = None) -> int:
    active_argv = sys.argv[1:] if argv is None else argv
    config_path = cli_config_path(active_argv)
    active_config_path = config_path or os.environ.get(CONFIG_PATH_ENV, "").strip() or None
    if config_path:
        os.environ[CONFIG_PATH_ENV] = config_path
    try:
        refresh_config(active_config_path)
        daemon.refresh_config(active_config_path)
        dashboard.refresh_config(active_config_path)
    except SidecarConfigError as exc:
        print_config_error("sidecar.py", exc)
        return 1
    parser = build_parser()
    args = parser.parse_args(active_argv)
    parsed_config_path = getattr(args, "config", None)
    if parsed_config_path and parsed_config_path != active_config_path:
        os.environ[CONFIG_PATH_ENV] = parsed_config_path
        try:
            refresh_config(parsed_config_path)
            daemon.refresh_config(parsed_config_path)
            dashboard.refresh_config(parsed_config_path)
        except SidecarConfigError as exc:
            print_config_error("sidecar.py", exc)
            return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
