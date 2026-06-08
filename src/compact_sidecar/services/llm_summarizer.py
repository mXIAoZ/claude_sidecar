from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from compact_sidecar.config import SidecarConfigError, load_config, load_config_for_import, load_config_safe

_CONFIG = load_config_for_import()
_LLM_CONFIG = _CONFIG["llm"]
DEFAULT_API_KEY_ENV = str(_LLM_CONFIG["api_key_env"])
DEFAULT_TIMEOUT_SECONDS = float(_LLM_CONFIG["timeout_seconds"])
DEFAULT_MAX_INPUT_CHARS = int(_LLM_CONFIG["max_input_chars"])
DEFAULT_MAX_OUTPUT_CHARS = int(_LLM_CONFIG["max_output_chars"])
MAX_ALLOWED_INPUT_CHARS = int(_LLM_CONFIG["max_allowed_input_chars"])
MAX_ALLOWED_OUTPUT_CHARS = int(_LLM_CONFIG["max_allowed_output_chars"])
RESPONSE_OVERHEAD_BYTES = int(_LLM_CONFIG["response_overhead_bytes"])
PROVIDER = str(_LLM_CONFIG["provider"])
SYSTEM_PROMPT = str(_LLM_CONFIG["system_prompt"])


class LLMSummaryConfigError(Exception):
    pass


class LLMSummaryRequestError(Exception):
    pass


@dataclass(frozen=True)
class LLMSummaryConfig:
    endpoint: str
    model: str
    api_key: str
    api_key_env: str = DEFAULT_API_KEY_ENV
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    response_overhead_bytes: int = RESPONSE_OVERHEAD_BYTES
    provider: str = PROVIDER
    system_prompt: str = SYSTEM_PROMPT

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "LLMSummaryConfig":
        env = os.environ if environ is None else environ
        try:
            config = load_config(environ=dict(env))
        except SidecarConfigError as exc:
            raise LLMSummaryConfigError(str(exc)) from exc
        return cls.from_config(config, env)

    @classmethod
    def from_config(cls, config: dict[str, Any], environ: dict[str, str] | None = None) -> "LLMSummaryConfig":
        env = os.environ if environ is None else environ
        llm_config = config.get("llm", {}) if isinstance(config, dict) else {}
        endpoint = str(llm_config.get("endpoint", "")).strip()
        model = str(llm_config.get("model", "")).strip()
        api_key_env = str(llm_config.get("api_key_env", DEFAULT_API_KEY_ENV)).strip() or DEFAULT_API_KEY_ENV
        api_key = env.get(api_key_env, "")

        if not endpoint:
            raise LLMSummaryConfigError("SIDECAR_LLM_ENDPOINT is required")
        if not model:
            raise LLMSummaryConfigError("SIDECAR_LLM_MODEL is required")
        if not api_key:
            raise LLMSummaryConfigError(f"LLM API key environment variable {api_key_env} is required")
        validate_endpoint(endpoint)

        return cls(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            api_key_env=api_key_env,
            timeout_seconds=float(llm_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            max_input_chars=parse_config_int("SIDECAR_LLM_MAX_INPUT_CHARS", llm_config.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS), MAX_ALLOWED_INPUT_CHARS),
            max_output_chars=parse_config_int("SIDECAR_LLM_MAX_OUTPUT_CHARS", llm_config.get("max_output_chars", DEFAULT_MAX_OUTPUT_CHARS), MAX_ALLOWED_OUTPUT_CHARS),
            response_overhead_bytes=parse_config_int("llm.response_overhead_bytes", llm_config.get("response_overhead_bytes", RESPONSE_OVERHEAD_BYTES)),
            provider=str(llm_config.get("provider", PROVIDER)),
            system_prompt=str(llm_config.get("system_prompt", SYSTEM_PROMPT)),
        )


@dataclass(frozen=True)
class LLMSummaryResult:
    summary_text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    input_chars: int
    output_chars: int
    model: str
    provider: str
    elapsed_ms: int


def parse_config_int(name: str, value: Any, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise LLMSummaryConfigError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMSummaryConfigError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise LLMSummaryConfigError(f"{name} must be positive")
    if maximum is not None and parsed > maximum:
        raise LLMSummaryConfigError(f"{name} must be at most {maximum}")
    return parsed


def parse_float_env(env: dict[str, str], name: str, default: float) -> float:
    value = env.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise LLMSummaryConfigError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise LLMSummaryConfigError(f"{name} must be positive")
    return parsed


def parse_int_env(env: dict[str, str], name: str, default: int, maximum: int | None = None) -> int:
    value = env.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LLMSummaryConfigError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise LLMSummaryConfigError(f"{name} must be positive")
    if maximum is not None and parsed > maximum:
        raise LLMSummaryConfigError(f"{name} must be at most {maximum}")
    return parsed


def summarize_with_openai_compatible(config: LLMSummaryConfig, prompt: str) -> LLMSummaryResult:
    validate_endpoint(config.endpoint)
    bounded_prompt = prompt[: config.max_input_chars]
    request_body = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": config.system_prompt,
            },
            {"role": "user", "content": bounded_prompt},
        ],
        "max_tokens": config.max_output_chars,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(
        config.endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    started = time.monotonic()
    byte_limit = response_byte_limit(config)
    try:
        with urllib_request.urlopen(request, timeout=config.timeout_seconds) as response:
            response_body = response.read(byte_limit + 1)
    except (OSError, urllib_error.URLError) as exc:
        raise LLMSummaryRequestError(f"LLM request failed: {safe_error_message(exc, config.api_key)}") from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if len(response_body) > byte_limit:
        raise LLMSummaryRequestError("LLM response exceeded maximum size")

    summary_text, usage = parse_streaming_chat_completion(response_body)
    if len(summary_text) > config.max_output_chars:
        raise LLMSummaryRequestError("LLM summary exceeded maximum output characters")
    return LLMSummaryResult(
        summary_text=summary_text,
        prompt_tokens=usage_int(usage, "prompt_tokens"),
        completion_tokens=usage_int(usage, "completion_tokens"),
        total_tokens=usage_int(usage, "total_tokens"),
        input_chars=len(bounded_prompt),
        output_chars=len(summary_text),
        model=config.model,
        provider=config.provider,
        elapsed_ms=elapsed_ms,
    )


def parse_streaming_chat_completion(response_body: bytes) -> tuple[str, Any]:
    try:
        text = response_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LLMSummaryRequestError("LLM streaming response was not valid UTF-8") from exc

    parts: list[str] = []
    usage: Any = None
    saw_event = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        saw_event = True
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMSummaryRequestError("LLM streaming response contained invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMSummaryRequestError("LLM streaming event must be an object")
        event_usage = payload.get("usage")
        if isinstance(event_usage, dict):
            usage = event_usage
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    parts.append(content)

    if not saw_event:
        raise LLMSummaryRequestError("LLM streaming response missing data events")
    summary_text = "".join(parts)
    if not summary_text:
        raise LLMSummaryRequestError("LLM streaming response missing content")
    return summary_text, usage


def usage_int(usage: Any, name: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(name)
    return value if isinstance(value, int) else None


def response_byte_limit(config: LLMSummaryConfig) -> int:
    return config.max_output_chars * 8 + config.response_overhead_bytes


def validate_endpoint(endpoint: str) -> None:
    parsed = urllib_parse.urlparse(endpoint)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname and is_loopback_host(parsed.hostname):
        return
    raise LLMSummaryConfigError("SIDECAR_LLM_ENDPOINT must use https unless it targets localhost or loopback")


def is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def safe_error_message(exc: BaseException, *secrets: str) -> str:
    message = str(exc)
    if not message:
        return exc.__class__.__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message
