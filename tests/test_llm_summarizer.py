from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_summarizer import (  # noqa: E402
    LLMSummaryConfig,
    LLMSummaryConfigError,
    LLMSummaryRequestError,
    summarize_with_openai_compatible,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict | bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        body = self.payload if isinstance(self.payload, bytes) else streaming_body(self.payload)
        return body if size is None else body[:size]


def streaming_body(payload: dict) -> bytes:
    body = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\ndata: [DONE]\n\n"
    return body.encode("utf-8")


class LLMSummarizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_config_reads_openai_compatible_environment(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                "SIDECAR_TEST_KEY": "secret-key",
                "SIDECAR_LLM_TIMEOUT_SECONDS": "12.5",
                "SIDECAR_LLM_MAX_INPUT_CHARS": "1234",
                "SIDECAR_LLM_MAX_OUTPUT_CHARS": "567",
            }
        )

        config = LLMSummaryConfig.from_env()

        self.assertEqual(config.endpoint, "https://llm.example.test/v1/chat/completions")
        self.assertEqual(config.model, "summary-model")
        self.assertEqual(config.api_key_env, "SIDECAR_TEST_KEY")
        self.assertEqual(config.api_key, "secret-key")
        self.assertEqual(config.timeout_seconds, 12.5)
        self.assertEqual(config.max_input_chars, 1234)
        self.assertEqual(config.max_output_chars, 567)

    def test_config_missing_api_key_names_env_without_secret_value(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
            }
        )

        with self.assertRaises(LLMSummaryConfigError) as raised:
            LLMSummaryConfig.from_env()

        message = str(raised.exception)
        self.assertIn("SIDECAR_TEST_KEY", message)
        self.assertNotIn("secret", message.lower())

    def test_config_rejects_non_loopback_http_endpoint(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "http://llm.example.test/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_TEST_KEY": "secret-key",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
            }
        )

        with self.assertRaises(LLMSummaryConfigError) as raised:
            LLMSummaryConfig.from_env()

        self.assertIn("https", str(raised.exception))

    def test_config_allows_loopback_http_endpoint(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "http://127.0.0.1:8080/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_TEST_KEY": "secret-key",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
            }
        )

        config = LLMSummaryConfig.from_env()

        self.assertEqual(config.endpoint, "http://127.0.0.1:8080/v1/chat/completions")

    def test_config_rejects_oversized_character_limits(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                "SIDECAR_TEST_KEY": "secret-key",
                "SIDECAR_LLM_MAX_INPUT_CHARS": "200001",
            }
        )

        with self.assertRaises(LLMSummaryConfigError) as raised:
            LLMSummaryConfig.from_env()

        self.assertIn("at most 200000", str(raised.exception))

    def test_config_rejects_oversized_output_limit(self) -> None:
        os.environ.update(
            {
                "SIDECAR_LLM_ENDPOINT": "https://llm.example.test/v1/chat/completions",
                "SIDECAR_LLM_MODEL": "summary-model",
                "SIDECAR_LLM_API_KEY_ENV": "SIDECAR_TEST_KEY",
                "SIDECAR_TEST_KEY": "secret-key",
                "SIDECAR_LLM_MAX_OUTPUT_CHARS": "50001",
            }
        )

        with self.assertRaises(LLMSummaryConfigError) as raised:
            LLMSummaryConfig.from_env()

        self.assertIn("at most 50000", str(raised.exception))

    def test_request_error_redacts_api_key_from_underlying_exception(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="SECRET_VALUE_SHOULD_NOT_LEAK",
            api_key_env="SIDECAR_TEST_KEY",
        )

        def fake_urlopen(request: object, timeout: float | None = None) -> FakeHTTPResponse:
            raise OSError("failed with SECRET_VALUE_SHOULD_NOT_LEAK in header")

        with patch("llm_summarizer.urllib_request.urlopen", fake_urlopen):
            with self.assertRaises(LLMSummaryRequestError) as raised:
                summarize_with_openai_compatible(config, "prompt")

        message = str(raised.exception)
        self.assertIn("[redacted]", message)
        self.assertNotIn("SECRET_VALUE_SHOULD_NOT_LEAK", message)

    def test_request_sends_chat_completion_and_parses_usage(self) -> None:
        captured: dict[str, object] = {}
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="secret-key",
            api_key_env="SIDECAR_TEST_KEY",
            timeout_seconds=3.0,
            max_input_chars=1000,
            max_output_chars=321,
        )
        payload = {
            "choices": [{"delta": {"content": "# Rolling Summary\n\n## Compact 前必须保留\nkeep"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        }

        def fake_urlopen(request: object, timeout: float | None = None) -> FakeHTTPResponse:
            captured["full_url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(payload)

        with patch("llm_summarizer.urllib_request.urlopen", fake_urlopen):
            result = summarize_with_openai_compatible(config, "summarize this")

        self.assertEqual(captured["full_url"], config.endpoint)
        self.assertEqual(captured["timeout"], config.timeout_seconds)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(captured["headers"]["Content-type"], "application/json")
        self.assertEqual(captured["headers"]["Accept"], "text/event-stream")
        self.assertEqual(captured["body"]["model"], "summary-model")
        self.assertEqual(captured["body"]["max_tokens"], 321)
        self.assertIs(captured["body"]["stream"], True)
        self.assertEqual(captured["body"]["stream_options"], {"include_usage": True})
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")
        self.assertEqual(captured["body"]["messages"][1], {"role": "user", "content": "summarize this"})
        self.assertEqual(result.summary_text, "# Rolling Summary\n\n## Compact 前必须保留\nkeep")
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 22)
        self.assertEqual(result.total_tokens, 33)
        self.assertEqual(result.input_chars, len("summarize this"))
        self.assertEqual(result.output_chars, len(result.summary_text))
        self.assertEqual(result.model, "summary-model")
        self.assertEqual(result.provider, "openai-compatible")
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_missing_usage_leaves_token_counts_unknown(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="secret-key",
            api_key_env="SIDECAR_TEST_KEY",
        )
        payload = {"choices": [{"delta": {"content": "summary"}}]}

        with patch("llm_summarizer.urllib_request.urlopen", return_value=FakeHTTPResponse(payload)):
            result = summarize_with_openai_compatible(config, "prompt")

        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)
        self.assertIsNone(result.total_tokens)

    def test_request_errors_do_not_leak_api_key(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="SECRET_VALUE_SHOULD_NOT_LEAK",
            api_key_env="SIDECAR_TEST_KEY",
        )

        def fake_urlopen(request: object, timeout: float | None = None) -> FakeHTTPResponse:
            raise OSError("connection failed for test")

        with patch("llm_summarizer.urllib_request.urlopen", fake_urlopen):
            with self.assertRaises(LLMSummaryRequestError) as raised:
                summarize_with_openai_compatible(config, "prompt")

        message = str(raised.exception)
        self.assertIn("request failed", message)
        self.assertNotIn("SECRET_VALUE_SHOULD_NOT_LEAK", message)

    def test_invalid_response_shape_is_reported(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="secret-key",
            api_key_env="SIDECAR_TEST_KEY",
        )

        with patch("llm_summarizer.urllib_request.urlopen", return_value=FakeHTTPResponse({"choices": []})):
            with self.assertRaises(LLMSummaryRequestError) as raised:
                summarize_with_openai_compatible(config, "prompt")

        self.assertIn("missing content", str(raised.exception))

    def test_output_character_limit_is_enforced_after_stream_parse(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="secret-key",
            api_key_env="SIDECAR_TEST_KEY",
            max_output_chars=3,
        )
        payload = {"choices": [{"delta": {"content": "abcd"}}]}

        with patch("llm_summarizer.urllib_request.urlopen", return_value=FakeHTTPResponse(payload)):
            with self.assertRaises(LLMSummaryRequestError) as raised:
                summarize_with_openai_compatible(config, "prompt")

        self.assertIn("maximum output characters", str(raised.exception))

    def test_oversized_response_is_rejected(self) -> None:
        config = LLMSummaryConfig(
            endpoint="https://llm.example.test/v1/chat/completions",
            model="summary-model",
            api_key="secret-key",
            api_key_env="SIDECAR_TEST_KEY",
            max_output_chars=1,
        )
        oversized_body = b"{" + (b"x" * 70_000)

        with patch("llm_summarizer.urllib_request.urlopen", return_value=FakeHTTPResponse(oversized_body)):
            with self.assertRaises(LLMSummaryRequestError) as raised:
                summarize_with_openai_compatible(config, "prompt")

        self.assertIn("maximum size", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
