from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.message import PathRef
from ..model.client import ModelClientError, OpenAICompatClient
from ..tools.fake import EchoArgsTool, NoopSuccessTool
from ..tools.registry import ToolRegistry
from ..util.trace import ArtifactStore


DEFAULT_SYSTEM_PROMPT = (
    "You are running the PhoneHarness M0a tool-call roundtrip probe. "
    "For the first response, call the echo_args tool exactly once with this exact payload: "
    '{"text":"roundtrip probe","count":1,"metadata":{"phase":"M0a","transport":"openai-compatible"}}. '
    "After the tool result is returned, reply with a short final sentence confirming the roundtrip worked. "
    "Do not call another tool after the first tool result."
)

DEFAULT_USER_PROMPT = "Run the M0a probe now."


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    api_key: str
    model: str
    output_dir: str | Path
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_prompt: str = DEFAULT_USER_PROMPT


@dataclass(frozen=True)
class ProbeOutcome:
    success: bool
    run_dir: PathRef
    trace_path: PathRef
    request_paths: tuple[PathRef, ...]
    response_paths: tuple[PathRef, ...]
    tool_call_paths: tuple[PathRef, ...]
    tool_result_paths: tuple[PathRef, ...]
    final_text: str
    provider_conclusion: str
    blocker: str | None = None


def run_m0a_probe(config: ProbeConfig) -> ProbeOutcome:
    artifacts = ArtifactStore(config.output_dir)
    registry = ToolRegistry([EchoArgsTool(), NoopSuccessTool()])
    client = OpenAICompatClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": config.user_prompt},
    ]
    request_paths: list[PathRef] = []
    response_paths: list[PathRef] = []
    tool_call_paths: list[PathRef] = []
    tool_result_paths: list[PathRef] = []

    artifacts.append_trace(
        "probe_started",
        {
            "model": config.model,
            "base_url": config.base_url,
            "tools": [tool["function"]["name"] for tool in registry.to_openai_tools()],
        },
    )

    first_result = _call_model(
        client=client,
        artifacts=artifacts,
        turn_index=1,
        messages=messages,
        tools=registry.to_openai_tools(),
        tool_choice={"type": "function", "function": {"name": "echo_args"}},
    )
    request_paths.append(first_result[0])
    response_paths.append(first_result[1])
    first_response_payload = first_result[2]

    try:
        assistant_message = _first_choice_message(first_response_payload)
    except ValueError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            blocker=str(exc),
        )

    tool_calls = assistant_message.get("tool_calls") or []
    artifacts.append_trace(
        "assistant_response",
        {
            "turn": 1,
            "content": assistant_message.get("content"),
            "tool_call_count": len(tool_calls),
        },
    )
    if not tool_calls:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            blocker="model returned no tool_calls on the first turn",
        )

    messages.append(
        {
            "role": "assistant",
            "content": assistant_message.get("content"),
            "tool_calls": tool_calls,
        }
    )

    for index, tool_call in enumerate(tool_calls, start=1):
        tool_name = ((tool_call.get("function") or {}).get("name")) or f"tool_{index}"
        tool_call_ref = artifacts.write_json(f"tool_call_{index:02d}_{tool_name}.json", tool_call)
        tool_call_paths.append(tool_call_ref)
        try:
            arguments, tool_result = registry.execute_tool_call(tool_call)
        except Exception as exc:
            return _failure_outcome(
                artifacts=artifacts,
                request_paths=request_paths,
                response_paths=response_paths,
                tool_call_paths=tool_call_paths,
                tool_result_paths=tool_result_paths,
                blocker=f"tool execution failed for {tool_name}: {exc}",
            )

        artifacts.append_trace(
            "tool_call",
            {
                "index": index,
                "name": tool_name,
                "arguments": arguments,
                "path": tool_call_ref.path,
            },
        )
        tool_result_payload = tool_result.to_dict()
        tool_result_ref = artifacts.write_json(f"tool_result_{index:02d}_{tool_name}.json", tool_result_payload)
        tool_result_paths.append(tool_result_ref)
        artifacts.append_trace(
            "tool_result",
            {
                "index": index,
                "name": tool_name,
                "result": tool_result_payload,
                "path": tool_result_ref.path,
            },
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "name": tool_name,
                "content": json.dumps(tool_result_payload, ensure_ascii=False),
            }
        )

    second_result = _call_model(
        client=client,
        artifacts=artifacts,
        turn_index=2,
        messages=messages,
        tools=None,
        tool_choice=None,
    )
    request_paths.append(second_result[0])
    response_paths.append(second_result[1])
    second_response_payload = second_result[2]

    try:
        final_message = _first_choice_message(second_response_payload)
    except ValueError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            blocker=str(exc),
        )

    final_text = (final_message.get("content") or "").strip()
    artifacts.append_trace(
        "assistant_response",
        {
            "turn": 2,
            "content": final_text,
            "tool_call_count": len(final_message.get("tool_calls") or []),
        },
    )
    success = bool(final_text)
    provider_conclusion = (
        "OpenAI-compatible provider completed a real M0a tool-call roundtrip: "
        "schema emitted, tool_calls returned, fake tool executed, tool result fed back, and final text produced."
        if success
        else "Provider returned tool_calls but did not produce a final text response after tool result feedback."
    )
    artifacts.append_trace(
        "probe_finished",
        {
            "success": success,
            "final_text": final_text,
            "provider_conclusion": provider_conclusion,
        },
    )

    summary_payload = {
        "success": success,
        "model": config.model,
        "base_url": config.base_url,
        "final_text": final_text,
        "request_paths": [ref.to_dict() for ref in request_paths],
        "response_paths": [ref.to_dict() for ref in response_paths],
        "tool_call_paths": [ref.to_dict() for ref in tool_call_paths],
        "tool_result_paths": [ref.to_dict() for ref in tool_result_paths],
        "provider_conclusion": provider_conclusion,
    }
    summary_ref = artifacts.write_json("summary.json", summary_payload)
    artifacts.append_trace("summary_written", {"path": summary_ref.path})

    paths = artifacts.paths()
    return ProbeOutcome(
        success=success,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        request_paths=tuple(request_paths),
        response_paths=tuple(response_paths),
        tool_call_paths=tuple(tool_call_paths),
        tool_result_paths=tuple(tool_result_paths),
        final_text=final_text,
        provider_conclusion=provider_conclusion,
        blocker=None if success else "final text was empty",
    )


def _call_model(
    *,
    client: OpenAICompatClient,
    artifacts: ArtifactStore,
    turn_index: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
) -> tuple[PathRef, PathRef, dict[str, Any]]:
    try:
        result = client.chat_completion(messages=messages, tools=tools, tool_choice=tool_choice)
    except ModelClientError as exc:
        artifacts.append_trace("model_error", {"turn": turn_index, "error": str(exc)})
        raise
    request_ref = artifacts.write_json(f"request_{turn_index:02d}.json", result.request_payload)
    response_ref = artifacts.write_json(f"response_{turn_index:02d}.json", result.response_payload)
    artifacts.append_trace(
        "model_io",
        {
            "turn": turn_index,
            "request_path": request_ref.path,
            "response_path": response_ref.path,
        },
    )
    return request_ref, response_ref, result.response_payload


def _first_choice_message(response_payload: dict[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices") or []
    if not choices:
        raise ValueError("model response contained no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("model response choice[0].message is missing")
    return message


def _failure_outcome(
    *,
    artifacts: ArtifactStore,
    request_paths: list[PathRef],
    response_paths: list[PathRef],
    tool_call_paths: list[PathRef],
    tool_result_paths: list[PathRef],
    blocker: str,
) -> ProbeOutcome:
    artifacts.append_trace("probe_finished", {"success": False, "blocker": blocker})
    paths = artifacts.paths()
    return ProbeOutcome(
        success=False,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        request_paths=tuple(request_paths),
        response_paths=tuple(response_paths),
        tool_call_paths=tuple(tool_call_paths),
        tool_result_paths=tuple(tool_result_paths),
        final_text="",
        provider_conclusion="OpenAI-compatible provider did not complete the full M0a tool-call roundtrip.",
        blocker=blocker,
    )
