from __future__ import annotations

import json
import os
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
        api_format: str | None = None,
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
        default_api_format = "responses" if self.base_url.endswith("/responses") else "chat_completions"
        self.api_format = _normalize_api_format(
            api_format
            or os.environ.get("PHONEHARNESS_OPENAI_API_FORMAT")
            or os.environ.get("OPENAI_API_FORMAT")
            or default_api_format
        )
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

        if self.api_format == "responses":
            responses_payload = _chat_payload_to_responses_payload(payload)
            response_payload = self._post_json(self._responses_url(), responses_payload)
            return ChatCompletionResult(
                request_payload=responses_payload,
                response_payload=_responses_payload_to_chat_payload(response_payload),
            )

        response_payload = self._post_json(self._chat_completions_url(), payload)
        return ChatCompletionResult(request_payload=payload, response_payload=response_payload)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw = ""
        response_payload: dict[str, Any] = {}
        for attempt in range(self.max_retries):
            http_request = request.Request(
                url,
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

        return response_payload

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _responses_url(self) -> str:
        if self.base_url.endswith("/responses"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/responses"
        return f"{self.base_url}/v1/responses"


def _normalize_api_format(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "chat": "chat_completions",
        "chat_completion": "chat_completions",
        "chat_completions": "chat_completions",
        "responses": "responses",
        "response": "responses",
    }
    if normalized not in aliases:
        raise ValueError(f"unsupported api_format: {value!r}")
    return aliases[normalized]


def _chat_payload_to_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    instructions, input_items = _chat_messages_to_responses_input(payload.get("messages") or [])
    responses_payload: dict[str, Any] = {
        "model": payload["model"],
        "input": input_items,
    }
    if instructions:
        responses_payload["instructions"] = instructions
    if "temperature" in payload:
        responses_payload["temperature"] = payload["temperature"]
    if "max_tokens" in payload:
        responses_payload["max_output_tokens"] = payload["max_tokens"]
    if payload.get("tools"):
        responses_payload["tools"] = [_chat_tool_to_responses_tool(tool) for tool in payload["tools"]]
    if payload.get("tool_choice") is not None:
        responses_payload["tool_choice"] = _chat_tool_choice_to_responses_tool_choice(payload["tool_choice"])
    return responses_payload


def _chat_messages_to_responses_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role in {"system", "developer"}:
            text = _content_to_text(content)
            if text:
                instructions.append(text)
            continue

        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": _content_to_responses_tool_output(content),
                }
            )
            continue

        tool_calls = message.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            text = _content_to_text(content)
            if text:
                input_items.append({"role": "assistant", "content": text})
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.get("id", ""),
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", "{}"),
                    }
                )
            continue

        if role in {"user", "assistant"}:
            input_items.append({"role": role, "content": _content_to_responses_message_content(content)})

    return "\n\n".join(instructions), input_items


def _chat_tool_to_responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function" or "function" not in tool:
        return dict(tool)
    function = tool["function"]
    converted: dict[str, Any] = {
        "type": "function",
        "name": function["name"],
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }
    if function.get("description"):
        converted["description"] = function["description"]
    return converted


def _chat_tool_choice_to_responses_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any]:
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") == "function":
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            return {"type": "function", "name": name}
    return dict(tool_choice)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def _content_to_responses_message_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    converted: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            converted.append({"type": "input_text", "text": str(item)})
            continue
        if item.get("type") == "text":
            converted.append({"type": "input_text", "text": str(item.get("text", ""))})
        elif item.get("type") == "image_url":
            image_url = item.get("image_url") or {}
            converted.append({"type": "input_image", "image_url": image_url.get("url", "")})
        else:
            converted.append(dict(item))
    return converted


def _content_to_responses_tool_output(content: Any) -> Any:
    if isinstance(content, list):
        return _content_to_responses_message_content(content)
    return "" if content is None else str(content)


def _responses_payload_to_chat_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for index, item in enumerate(response_payload.get("output") or []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            content_parts.extend(_responses_message_content_to_text_parts(item.get("content") or []))
        elif item_type == "function_call":
            call_id = item.get("call_id") or item.get("id") or f"call_{index}"
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments") or "{}",
                    },
                }
            )

    if not content_parts and response_payload.get("output_text"):
        content_parts.append(str(response_payload["output_text"]))

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(part for part in content_parts if part),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = response_payload.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

    return {
        "id": response_payload.get("id", ""),
        "object": "chat.completion",
        "created": response_payload.get("created_at", 0),
        "model": response_payload.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else _responses_finish_reason(response_payload),
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "_raw_responses_payload": response_payload,
    }


def _responses_message_content_to_text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if "text" in item:
                parts.append(str(item["text"]))
            elif "refusal" in item:
                parts.append(str(item["refusal"]))
    return parts


def _responses_finish_reason(response_payload: dict[str, Any]) -> str:
    status = response_payload.get("status")
    if status == "incomplete":
        reason = (response_payload.get("incomplete_details") or {}).get("reason")
        if reason == "max_output_tokens":
            return "length"
    return "stop"
