from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class ModelClientError(RuntimeError):
    pass


_TRANSIENT_HTTP_CODES = {404, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ChatCompletionResult:
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 300.0,
        max_retries: int = 5,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not api_key:
            raise ValueError("api_key is required")
        if not model:
            raise ValueError("model is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)

    def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> ChatCompletionResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw = ""
        response_payload: dict[str, Any] = {}
        for attempt in range(self.max_retries):
            http_request = request.Request(
                self._chat_completions_url(),
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                response_payload = json.loads(raw)
                break
            except error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                if exc.code not in _TRANSIENT_HTTP_CODES or attempt == self.max_retries - 1:
                    raise ModelClientError(f"HTTP {exc.code}: {raw}") from exc
            except (error.URLError, TimeoutError) as exc:
                if attempt == self.max_retries - 1:
                    raise ModelClientError(f"request failed after {self.max_retries} attempts: {exc}") from exc
            except json.JSONDecodeError as exc:
                if attempt == self.max_retries - 1:
                    raise ModelClientError(f"invalid JSON response after {self.max_retries} attempts: {raw}") from exc
            time.sleep(2 ** attempt)

        return ChatCompletionResult(request_payload=payload, response_payload=response_payload)

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"
