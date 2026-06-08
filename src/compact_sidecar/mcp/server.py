from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from compact_sidecar import api as sidecar_api

JSONRPC_VERSION = "2.0"
SERVER_INFO = {"name": "sidecar-mcp", "version": "0.1.0"}


def _string_schema(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _boolean_schema(description: str, *, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


def _integer_schema(description: str, *, minimum: int = 1) -> dict[str, Any]:
    return {"type": "integer", "description": description, "minimum": minimum}


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "sidecar_status": {
        "name": "sidecar_status",
        "description": "Read sidecar runtime status without writing files.",
        "inputSchema": _object_schema({"config_path": _string_schema("Optional sidecar config JSON path.")}),
    },
    "sidecar_dashboard": {
        "name": "sidecar_dashboard",
        "description": "Read a sanitized dashboard snapshot without raw prompt or summary content by default.",
        "inputSchema": _object_schema(
            {
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "log_limit": _integer_schema("Maximum operation records to include."),
                "show_content": _boolean_schema("Include explicitly logged raw prompt/summary content.", default=False),
            }
        ),
    },
    "sidecar_config_validate": {
        "name": "sidecar_config_validate",
        "description": "Validate sidecar configuration and return safe metadata only.",
        "inputSchema": _object_schema({"config_path": _string_schema("Optional sidecar config JSON path.")}),
    },
    "sidecar_operation_log": {
        "name": "sidecar_operation_log",
        "description": "Read operation-log metadata without raw prompt or summary content by default.",
        "inputSchema": _object_schema(
            {
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "limit": _integer_schema("Maximum records to include."),
                "include_rotated": _boolean_schema("Include rotated operation-log records.", default=True),
                "show_content": _boolean_schema("Include explicitly logged raw prompt/summary content.", default=False),
            }
        ),
    },
    "sidecar_setup_rehearsal": {
        "name": "sidecar_setup_rehearsal",
        "description": "Install hooks and optionally write a daemon plist only to explicit rehearsal paths; never calls launchctl or tmux.",
        "inputSchema": _object_schema(
            {
                "settings_path": _string_schema("Explicit temporary or caller-provided settings JSON path to write."),
                "runtime_dir": _string_schema("Explicit temporary or caller-provided runtime directory."),
                "plist_path": _string_schema("Optional explicit daemon plist path to write."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "interval_seconds": _integer_schema("Daemon interval seconds for plist generation."),
            },
            ["settings_path", "runtime_dir"],
        ),
    },
    "sidecar_daemon_plist_rehearsal": {
        "name": "sidecar_daemon_plist_rehearsal",
        "description": "Write and inspect a launchd plist artifact at an explicit path; never calls launchctl.",
        "inputSchema": _object_schema(
            {
                "plist_path": _string_schema("Explicit temporary or caller-provided plist path to write."),
                "runtime_dir": _string_schema("Explicit temporary or caller-provided runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "interval_seconds": _integer_schema("Daemon interval seconds for plist generation."),
            },
            ["plist_path", "runtime_dir"],
        ),
    },
    "sidecar_daemon_status": {
        "name": "sidecar_daemon_status",
        "description": "Inspect an explicit daemon plist artifact without launchctl.",
        "inputSchema": _object_schema(
            {
                "plist_path": _string_schema("Explicit daemon plist path to inspect."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
            },
            ["plist_path"],
        ),
    },
    "sidecar_compact_plan_preview": {
        "name": "sidecar_compact_plan_preview",
        "description": "Preview compact controller readiness without sending tmux keys or writing runtime files.",
        "inputSchema": _object_schema(
            {
                "runtime_dir": _string_schema("Explicit runtime directory to inspect."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "prompt_chars": _integer_schema("Number of prompt characters to include in pressure estimate.", minimum=0),
                "min_readiness": _string_schema("Minimum readiness level that would trigger compact."),
            },
            ["runtime_dir"],
        ),
    },
    "sidecar_hook_install": {
        "name": "sidecar_hook_install",
        "description": "Install sidecar hooks into an explicit settings file; requires confirm=true and global settings opt-in if applicable.",
        "inputSchema": _object_schema(
            {
                "settings_path": _string_schema("Explicit settings JSON path to modify."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to perform the write."),
                "allow_global_settings": _boolean_schema("Required true when settings_path is ~/.claude/settings.json."),
            },
            ["settings_path", "confirm"],
        ),
    },
    "sidecar_hook_uninstall": {
        "name": "sidecar_hook_uninstall",
        "description": "Remove sidecar hooks from an explicit settings file; requires confirm=true and global settings opt-in if applicable.",
        "inputSchema": _object_schema(
            {
                "settings_path": _string_schema("Explicit settings JSON path to modify."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to perform the write."),
                "allow_global_settings": _boolean_schema("Required true when settings_path is ~/.claude/settings.json."),
            },
            ["settings_path", "confirm"],
        ),
    },
    "sidecar_daemon_plist_write": {
        "name": "sidecar_daemon_plist_write",
        "description": "Write a launchd plist artifact to an explicit path; requires confirm=true and never calls launchctl.",
        "inputSchema": _object_schema(
            {
                "plist_path": _string_schema("Explicit plist path to write."),
                "runtime_dir": _string_schema("Explicit runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to perform the write."),
                "interval_seconds": _integer_schema("Daemon interval seconds for plist generation."),
            },
            ["plist_path", "runtime_dir", "confirm"],
        ),
    },
    "sidecar_daemon_plist_remove": {
        "name": "sidecar_daemon_plist_remove",
        "description": "Remove a valid generated sidecar plist artifact from an explicit path; requires confirm=true and never calls launchctl.",
        "inputSchema": _object_schema(
            {
                "plist_path": _string_schema("Explicit plist path to remove."),
                "runtime_dir": _string_schema("Explicit runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to perform the write."),
            },
            ["plist_path", "runtime_dir", "confirm"],
        ),
    },
    "sidecar_daemon_run_once": {
        "name": "sidecar_daemon_run_once",
        "description": "Run one bounded daemon maintenance pass in an explicit runtime; requires confirm=true and does not expose daemon loop.",
        "inputSchema": _object_schema(
            {
                "runtime_dir": _string_schema("Explicit runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to perform the run."),
                "operation_log": _boolean_schema("Append metadata-only daemon operation log records."),
            },
            ["runtime_dir", "confirm"],
        ),
    },
    "sidecar_launchctl_lifecycle": {
        "name": "sidecar_launchctl_lifecycle",
        "description": "Run an explicit launchctl lifecycle action after plist validation; requires confirm=true.",
        "inputSchema": _object_schema(
            {
                "action": _string_schema("One of bootstrap, kickstart, status, bootout."),
                "plist_path": _string_schema("Explicit plist path to validate/use."),
                "runtime_dir": _string_schema("Explicit runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to invoke launchctl."),
                "operation_log": _boolean_schema("Append metadata-only daemon operation log records."),
            },
            ["action", "plist_path", "runtime_dir", "confirm"],
        ),
    },
    "sidecar_tmux_compact": {
        "name": "sidecar_tmux_compact",
        "description": "Run the explicit tmux compact controller with no-send by default; sending requires confirm=true, pane, and no_send=false.",
        "inputSchema": _object_schema(
            {
                "runtime_dir": _string_schema("Explicit runtime directory."),
                "config_path": _string_schema("Optional sidecar config JSON path."),
                "confirm": _boolean_schema("Required true to run the controller."),
                "pane": _string_schema("Explicit tmux pane required when no_send=false."),
                "prompt_path": _string_schema("Optional prompt file path; content is not returned."),
                "no_send": _boolean_schema("Preview without sending tmux keys.", default=True),
                "tmux_path": _string_schema("Optional tmux binary path; tests should use a fake tmux."),
                "operation_log": _boolean_schema("Append metadata-only controller operation log records."),
                "log_raw_prompt": _boolean_schema("Store bounded raw prompt only when operation_log=true; sensitive."),
                "min_readiness": _string_schema("Minimum readiness level that triggers compact."),
            },
            ["runtime_dir", "confirm"],
        ),
    },
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the compact sidecar MCP server.")
    parser.add_argument("--self-test", action="store_true", help="Validate that the MCP entry point can start.")
    return parser.parse_args(argv)


def text_content(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]


def _bool_arg(arguments: dict[str, Any], name: str, *, default: bool = False) -> bool:
    if name not in arguments:
        return default
    value = arguments[name]
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "sidecar_status": lambda args: sidecar_api.status_snapshot(args.get("config_path")),
        "sidecar_dashboard": lambda args: sidecar_api.dashboard_snapshot(
            args.get("config_path"),
            log_limit=args.get("log_limit"),
            show_content=_bool_arg(args, "show_content", default=False),
        ),
        "sidecar_config_validate": lambda args: sidecar_api.validate_config(args.get("config_path")),
        "sidecar_operation_log": lambda args: sidecar_api.operation_log_snapshot(
            args.get("config_path"),
            limit=args.get("limit"),
            include_rotated=_bool_arg(args, "include_rotated", default=True),
            show_content=_bool_arg(args, "show_content", default=False),
        ),
        "sidecar_setup_rehearsal": lambda args: sidecar_api.setup_rehearsal(
            args.get("settings_path"),
            args.get("runtime_dir"),
            args.get("plist_path"),
            args.get("config_path"),
            interval_seconds=args.get("interval_seconds"),
        ),
        "sidecar_daemon_plist_rehearsal": lambda args: sidecar_api.daemon_plist_rehearsal(
            args.get("plist_path"),
            args.get("runtime_dir"),
            args.get("config_path"),
            interval_seconds=args.get("interval_seconds"),
        ),
        "sidecar_daemon_status": lambda args: sidecar_api.daemon_agent_status(
            args.get("plist_path"),
            args.get("config_path"),
        ),
        "sidecar_compact_plan_preview": lambda args: sidecar_api.compact_plan_preview(
            args.get("runtime_dir"),
            args.get("config_path"),
            prompt_chars=int(args.get("prompt_chars", 0)),
            min_readiness=args.get("min_readiness"),
        ),
        "sidecar_hook_install": lambda args: sidecar_api.hook_install_mutation(
            args.get("settings_path"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            allow_global_settings=_bool_arg(args, "allow_global_settings", default=False),
        ),
        "sidecar_hook_uninstall": lambda args: sidecar_api.hook_uninstall_mutation(
            args.get("settings_path"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            allow_global_settings=_bool_arg(args, "allow_global_settings", default=False),
        ),
        "sidecar_daemon_plist_write": lambda args: sidecar_api.daemon_plist_write_mutation(
            args.get("plist_path"),
            args.get("runtime_dir"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            interval_seconds=args.get("interval_seconds"),
        ),
        "sidecar_daemon_plist_remove": lambda args: sidecar_api.daemon_plist_remove_mutation(
            args.get("plist_path"),
            args.get("runtime_dir"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
        ),
        "sidecar_daemon_run_once": lambda args: sidecar_api.daemon_run_once_mutation(
            args.get("runtime_dir"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            operation_log_enabled=_bool_arg(args, "operation_log", default=False),
        ),
        "sidecar_launchctl_lifecycle": lambda args: sidecar_api.launchctl_lifecycle_mutation(
            str(args.get("action") or ""),
            args.get("plist_path"),
            args.get("runtime_dir"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            operation_log_enabled=_bool_arg(args, "operation_log", default=False),
        ),
        "sidecar_tmux_compact": lambda args: sidecar_api.tmux_compact_mutation(
            args.get("runtime_dir"),
            args.get("config_path"),
            confirm=_bool_arg(args, "confirm", default=False),
            pane=args.get("pane"),
            prompt_path=args.get("prompt_path"),
            no_send=_bool_arg(args, "no_send", default=True),
            tmux_path=args.get("tmux_path"),
            operation_log_enabled=_bool_arg(args, "operation_log", default=False),
            log_raw_prompt=_bool_arg(args, "log_raw_prompt", default=False),
            min_readiness=args.get("min_readiness"),
        ),
    }
    if name not in handlers:
        raise KeyError(f"unknown tool: {name}")
    return handlers[name](arguments)

def result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": code, "message": message}}


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return result_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        return result_response(request_id, {"tools": list(TOOL_DEFINITIONS.values())})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(name, str):
            return error_response(request_id, -32602, "tools/call requires a string tool name")
        try:
            payload = call_tool(name, arguments)
        except KeyError as exc:
            return error_response(request_id, -32601, str(exc))
        except ValueError as exc:
            return error_response(request_id, -32602, str(exc))
        except Exception as exc:
            return error_response(request_id, -32000, f"tool failed: {type(exc).__name__}: {exc}")
        return result_response(request_id, {"content": text_content(payload)})
    return error_response(request_id, -32601, f"method not found: {method}")


def run_stdio() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps(error_response(None, -32700, f"parse error: {exc}"), sort_keys=True), flush=True)
            continue
        if not isinstance(request, dict):
            print(json.dumps(error_response(None, -32600, "request must be an object"), sort_keys=True), flush=True)
            continue
        response = handle_request(request)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        print(json.dumps({"ok": True, "server": SERVER_INFO["name"], "tools": sorted(TOOL_DEFINITIONS)}, sort_keys=True))
        return 0
    return run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
