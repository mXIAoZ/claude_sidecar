from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse

CONFIG_PATH_ENV = "SIDECAR_CONFIG_PATH"
RUNTIME_DIR_ENV = "SIDECAR_COMPACT_DIR"
RUNTIME_CONFIG_NAME = "sidecar.config.json"
TEMPLATE_NAME = "sidecar.config.template.json"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SidecarConfigError(Exception):
    pass


def project_root() -> Path:
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / TEMPLATE_NAME).is_file():
            return parent
    return module_path.parents[2]


def source_tree_pythonpath() -> str | None:
    src_path = project_root() / "src"
    return str(src_path) if (src_path / "compact_sidecar").is_dir() else None


def template_path() -> Path:
    candidates = [
        project_root() / TEMPLATE_NAME,
        Path(__file__).resolve().with_name(TEMPLATE_NAME),
        Path(sys.prefix) / TEMPLATE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def runtime_project_root(start: Path | None = None, config: dict[str, Any] | None = None) -> Path:
    active_config = load_template() if config is None else config
    markers = tuple(str(marker) for marker in active_config["paths"]["project_root_markers"])
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return current


def default_runtime_dir(
    environ: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    start: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get(RUNTIME_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    active_config = load_template() if config is None else config
    config_runtime = str(active_config["paths"].get("runtime_dir") or "")
    if config_runtime:
        return Path(config_runtime).expanduser()
    return runtime_project_root(start, active_config) / str(active_config["paths"]["default_runtime_dir_name"])


def runtime_config_path(
    environ: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    start: Path | None = None,
) -> Path:
    return default_runtime_dir(environ, config, start) / RUNTIME_CONFIG_NAME


def default_config_path(environ: dict[str, str] | None = None, start: Path | None = None) -> Path | None:
    path = runtime_config_path(environ, start=start)
    return path if path.is_file() else None


def copy_default_config_to_runtime(
    config: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Path, bool]:
    target = runtime_config_path(environ, config)
    if target.exists():
        return target, False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template_path().read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        raise SidecarConfigError(f"failed to write runtime config {target}: {exc}") from exc
    return target, True


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SidecarConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise SidecarConfigError(f"failed to read config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SidecarConfigError(f"failed to parse config file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SidecarConfigError("config root must be a JSON object")
    return payload


def load_template() -> dict[str, Any]:
    return _read_json(template_path())


def explicit_config_path(config_path: str | Path | None = None, environ: dict[str, str] | None = None) -> Path | None:
    if config_path is not None and str(config_path):
        return Path(config_path).expanduser()
    env = os.environ if environ is None else environ
    configured = env.get(CONFIG_PATH_ENV, "").strip()
    return Path(configured).expanduser() if configured else None


def deep_merge(base: dict[str, Any], override: dict[str, Any], *, path: str = "") -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        dotted = f"{path}.{key}" if path else key
        if key not in merged:
            raise SidecarConfigError(f"unknown config key: {dotted}")
        current = merged[key]
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value, path=dotted)
        elif isinstance(current, dict) != isinstance(value, dict):
            raise SidecarConfigError(f"config key {dotted} must be an object")
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def parse_bool_env(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _set_path(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node: dict[str, Any] = config
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            raise SidecarConfigError(f"config key {'.'.join(path[:-1])} must be an object")
        node = child
    node[path[-1]] = value


def apply_env_overrides(config: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    merged = copy.deepcopy(config)
    mappings: tuple[tuple[str, tuple[str, ...], Any], ...] = (
        ("SIDECAR_COMPACT_DIR", ("paths", "runtime_dir"), str),
        ("SIDECAR_INJECT_ALWAYS", ("summary", "inject_always"), parse_bool_env),
        ("SIDECAR_OPERATION_LOG", ("operation_log", "enabled_by_default"), parse_bool_env),
        ("SIDECAR_LOG_RAW_SUMMARY", ("operation_log", "raw_summary_logged_by_default"), parse_bool_env),
        ("SIDECAR_LAUNCHCTL_PATH", ("daemon_launchd", "launchctl_path"), str),
        ("SIDECAR_LLM_ENDPOINT", ("llm", "endpoint"), str),
        ("SIDECAR_LLM_MODEL", ("llm", "model"), str),
        ("SIDECAR_LLM_API_KEY_ENV", ("llm", "api_key_env"), str),
        ("SIDECAR_LLM_TIMEOUT_SECONDS", ("llm", "timeout_seconds"), float),
        ("SIDECAR_LLM_MAX_INPUT_CHARS", ("llm", "max_input_chars"), int),
        ("SIDECAR_LLM_MAX_OUTPUT_CHARS", ("llm", "max_output_chars"), int),
    )
    for env_name, config_path, caster in mappings:
        raw = env.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            value = caster(raw)
        except ValueError as exc:
            raise SidecarConfigError(f"{env_name} has invalid value") from exc
        _set_path(merged, config_path, value)
    return merged


def _is_safe_file_name(value: str) -> bool:
    path = Path(value)
    return bool(value) and value not in {".", ".."} and not path.is_absolute() and path.name == value and "/" not in value and "\\" not in value


def require_file_name(value: str, dotted: str) -> str:
    if not _is_safe_file_name(value):
        raise SidecarConfigError(f"{dotted} must be a file name")
    return value


def require_env_name(value: str, dotted: str) -> str:
    if not ENV_NAME_RE.match(value):
        raise SidecarConfigError(f"{dotted} must be an environment variable name")
    return value


def require_plist_file_mode(value: str, dotted: str) -> int:
    try:
        mode = int(value, 8)
    except ValueError as exc:
        raise SidecarConfigError(f"{dotted} must be an octal file mode") from exc
    if mode < 0 or mode & 0o077:
        raise SidecarConfigError(f"{dotted} must not grant group or other permissions")
    return mode


def require_secret_safe_endpoint(value: str, dotted: str) -> None:
    if not value:
        return
    parsed = urllib_parse.urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SidecarConfigError(f"{dotted} must not include credentials, query, or fragment")


def get_file_name(config: dict[str, Any], dotted: str) -> str:
    return require_file_name(get_str(config, dotted), dotted)


def validate_file_name_dict(config: dict[str, Any], dotted: str) -> None:
    values = get_dict(config, dotted)
    for key, value in values.items():
        if not isinstance(value, str):
            raise SidecarConfigError(f"config key {dotted}.{key} must be a string")
        require_file_name(value, f"{dotted}.{key}")


def validate_file_name_list(config: dict[str, Any], dotted: str) -> None:
    values = get_list(config, dotted, str)
    for index, value in enumerate(values):
        require_file_name(value, f"{dotted}[{index}]")


def validate_config(config: dict[str, Any], *, allow_sensitive_defaults: bool = False) -> None:
    if get_int(config, "schema_version") != 1:
        raise SidecarConfigError("schema_version must be 1")
    required_categories = (
        "environment",
        "paths",
        "hooks",
        "summary",
        "readiness",
        "history_candidates",
        "operation_log",
        "llm",
        "daemon_launchd",
        "controller",
        "dashboard_status",
        "cli_defaults",
        "testing_diagnostics",
    )
    for category in required_categories:
        get_dict(config, category)

    get_file_name(config, "paths.default_runtime_dir_name")
    validate_file_name_list(config, "paths.project_root_markers")
    get_str(config, "paths.claude_settings_path")
    get_str(config, "paths.python_executable")
    validate_file_name_dict(config, "paths.runtime_files")
    validate_file_name_dict(config, "paths.scripts")
    get_file_name(config, "paths.summary_backup_prefix")

    entries = get_list(config, "hooks.entries", dict)
    for index, entry in enumerate(entries):
        prefix = f"hooks.entries[{index}]"
        for key in ("event", "matcher", "status_message"):
            if not isinstance(entry.get(key), str):
                raise SidecarConfigError(f"config key {prefix}.{key} must be a string")
        script = entry.get("script")
        if not isinstance(script, str):
            raise SidecarConfigError(f"config key {prefix}.script must be a string")
        timeout = entry.get("timeout")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise SidecarConfigError(f"config key {prefix}.timeout must be positive")
        require_file_name(script, f"{prefix}.script")
    get_list(config, "hooks.prompt_fields", str)
    positive_int(config, "hooks.userprompt_stdin_max_chars")
    positive_int(config, "hooks.postcompact_payload_max_chars")

    max_summary_chars = positive_int(config, "summary.max_summary_chars")
    head_chars = positive_int(config, "summary.head_chars")
    tail_chars = positive_int(config, "summary.tail_chars")
    get_bool(config, "summary.inject_always")
    if head_chars + tail_chars > max_summary_chars:
        raise SidecarConfigError("summary.head_chars plus summary.tail_chars exceeds summary.max_summary_chars")

    positive_int(config, "readiness.medium_chars")
    positive_int(config, "readiness.high_chars")
    levels = get_list(config, "readiness.levels", str)
    for level in ("low", "medium", "high", "attention"):
        if level not in levels:
            raise SidecarConfigError(f"readiness.levels must include {level}")
    validate_file_name_list(config, "readiness.runtime_pressure_files")

    positive_int(config, "history_candidates.history_max_bytes")
    positive_int(config, "history_candidates.candidate_limit")
    positive_int(config, "history_candidates.hint_limit")
    positive_int(config, "history_candidates.draft_summary_limit")
    validate_file_name_list(config, "history_candidates.history_names")

    get_file_name(config, "operation_log.file_name")
    get_file_name(config, "operation_log.rotated_file_name")
    positive_int(config, "operation_log.schema_version")
    positive_int(config, "operation_log.max_bytes")
    positive_int(config, "operation_log.max_raw_content_chars")
    if get_bool(config, "operation_log.raw_summary_logged_by_default") and not allow_sensitive_defaults:
        raise SidecarConfigError("operation_log.raw_summary_logged_by_default must remain false")

    require_secret_safe_endpoint(get_str(config, "llm.endpoint"), "llm.endpoint")
    require_env_name(get_str(config, "llm.api_key_env"), "llm.api_key_env")
    positive_float(config, "llm.timeout_seconds")
    positive_int(config, "llm.max_input_chars")
    positive_int(config, "llm.max_output_chars")
    positive_int(config, "llm.max_allowed_input_chars")
    positive_int(config, "llm.max_allowed_output_chars")
    if get_int(config, "llm.max_input_chars") > get_int(config, "llm.max_allowed_input_chars"):
        raise SidecarConfigError(f"SIDECAR_LLM_MAX_INPUT_CHARS must be at most {get_int(config, 'llm.max_allowed_input_chars')}")
    if get_int(config, "llm.max_output_chars") > get_int(config, "llm.max_allowed_output_chars"):
        raise SidecarConfigError(f"SIDECAR_LLM_MAX_OUTPUT_CHARS must be at most {get_int(config, 'llm.max_allowed_output_chars')}")

    get_file_name(config, "daemon_launchd.state_file")
    get_file_name(config, "daemon_launchd.stdout_file")
    get_file_name(config, "daemon_launchd.stderr_file")
    require_plist_file_mode(get_str(config, "daemon_launchd.plist_file_mode"), "daemon_launchd.plist_file_mode")
    positive_int(config, "daemon_launchd.default_interval_seconds")
    if get_bool(config, "daemon_launchd.run_at_load"):
        raise SidecarConfigError("daemon_launchd.run_at_load must remain false")
    if get_bool(config, "daemon_launchd.keep_alive"):
        raise SidecarConfigError("daemon_launchd.keep_alive must remain false")
    get_list(config, "daemon_launchd.program_args", str)

    positive_float(config, "controller.wait_timeout_seconds")
    positive_float(config, "controller.poll_interval_seconds")
    min_readiness = get_str(config, "controller.min_readiness")
    if min_readiness not in levels:
        raise SidecarConfigError("controller.min_readiness must be listed in readiness.levels")
    get_bool(config, "controller.no_send")
    get_bool(config, "controller.operation_log")
    if get_bool(config, "controller.log_raw_prompt") and not allow_sensitive_defaults:
        raise SidecarConfigError("controller.log_raw_prompt must remain false")

    positive_int(config, "dashboard_status.log_limit")
    positive_float(config, "dashboard_status.watch_interval_seconds")
    if get_bool(config, "dashboard_status.show_content") and not allow_sensitive_defaults:
        raise SidecarConfigError("dashboard_status.show_content must remain false")
    validate_file_name_list(config, "dashboard_status.known_files_order")


def cli_config_path(argv: list[str] | None = None) -> str | None:
    args = sys.argv[1:] if argv is None else argv
    for index, value in enumerate(args):
        if value == "--config" and index + 1 < len(args):
            return args[index + 1]
        if value.startswith("--config="):
            return value.split("=", 1)[1]
    return None


def load_config(config_path: str | Path | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    config = load_template()
    path = explicit_config_path(config_path or cli_config_path(), environ)
    if path is None:
        path = default_config_path(environ)
    if path is not None:
        config = deep_merge(config, _read_json(path))
        config["_config_path"] = str(path)
    validate_config(config)
    config = apply_env_overrides(config, environ)
    validate_config(config, allow_sensitive_defaults=True)
    return config


def load_config_safe(config_path: str | Path | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    if config_path is not None and str(config_path):
        return load_config(config_path, environ)
    if env.get(CONFIG_PATH_ENV, "").strip():
        return load_config(None, environ)
    try:
        return load_config(None, environ)
    except SidecarConfigError:
        return load_template()


def load_config_for_import(environ: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        return load_config_safe(None, environ)
    except SidecarConfigError:
        return load_template()


def load_config_for_cli(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    config = load_template()
    path = explicit_config_path(cli_config_path(argv), environ)
    if path is None:
        path = default_config_path(environ)
    if path is not None:
        config = deep_merge(config, _read_json(path))
        config["_config_path"] = str(path)
    validate_config(config)
    config = apply_env_overrides(config, environ)
    validate_config(config, allow_sensitive_defaults=True)
    return config


def print_config_error(program: str, exc: SidecarConfigError) -> None:
    print(f"{program}: {exc}", file=sys.stderr)


def _lookup(config: dict[str, Any], dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise SidecarConfigError(f"missing config key: {dotted}")
        node = node[part]
    return node


def get_value(config: dict[str, Any], dotted: str) -> Any:
    return _lookup(config, dotted)


def get_dict(config: dict[str, Any], dotted: str) -> dict[str, Any]:
    value = _lookup(config, dotted)
    if not isinstance(value, dict):
        raise SidecarConfigError(f"config key {dotted} must be an object")
    return value


def get_list(config: dict[str, Any], dotted: str, item_type: type | tuple[type, ...] | None = None) -> list[Any]:
    value = _lookup(config, dotted)
    if not isinstance(value, list):
        raise SidecarConfigError(f"config key {dotted} must be a list")
    if item_type is not None and not all(isinstance(item, item_type) for item in value):
        raise SidecarConfigError(f"config key {dotted} has invalid item type")
    return value


def get_str(config: dict[str, Any], dotted: str) -> str:
    value = _lookup(config, dotted)
    if not isinstance(value, str):
        raise SidecarConfigError(f"config key {dotted} must be a string")
    return value


def get_int(config: dict[str, Any], dotted: str) -> int:
    value = _lookup(config, dotted)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SidecarConfigError(f"config key {dotted} must be an integer")
    return value


def get_float(config: dict[str, Any], dotted: str) -> float:
    value = _lookup(config, dotted)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SidecarConfigError(f"config key {dotted} must be a number")
    return float(value)


def get_bool(config: dict[str, Any], dotted: str) -> bool:
    value = _lookup(config, dotted)
    if not isinstance(value, bool):
        raise SidecarConfigError(f"config key {dotted} must be a boolean")
    return value


def positive_int(config: dict[str, Any], dotted: str) -> int:
    value = get_int(config, dotted)
    if value <= 0:
        raise SidecarConfigError(f"config key {dotted} must be positive")
    return value


def positive_float(config: dict[str, Any], dotted: str) -> float:
    value = get_float(config, dotted)
    if value <= 0:
        raise SidecarConfigError(f"config key {dotted} must be positive")
    return value


def optional_path(config: dict[str, Any], dotted: str) -> Path | None:
    value = get_str(config, dotted)
    return Path(value).expanduser() if value else None


def config_path_env(config: dict[str, Any]) -> dict[str, str]:
    path = config.get("_config_path")
    return {CONFIG_PATH_ENV: str(path)} if isinstance(path, str) and path else {}
