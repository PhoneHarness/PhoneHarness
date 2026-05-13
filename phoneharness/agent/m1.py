from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..backend import BackendCommandResult
from ..model.client import ModelClientError, OpenAICompatClient
from ..tools.registry import ToolRegistry
from ..tools.shell_exec import ShellExecTool
from ..util.trace import ArtifactStore
from .message import PathRef, ToolResult


DEFAULT_SYSTEM_PROMPT = (
    "You are the PhoneHarness M1 controller. "
    "You have exactly one tool, shell_exec, which runs a shell command in the device Ubuntu environment "
    "through adb -> run-as -> Termux -> proot Ubuntu. "
    "When the user asks to execute a command on the device, first call shell_exec exactly once with JSON "
    '{"command":"<raw command>"}. '
    "Extract the simplest raw command from the user request and do not wrap it in extra scripts unless the user asked for that. "
    "After the tool result is returned, reply with one short sentence summarizing whether it succeeded and the key stdout. "
    "Do not call another tool after the first tool result."
)


@dataclass(frozen=True)
class M1RunConfig:
    user_input: str
    base_url: str
    api_key: str
    model: str
    output_dir: str | Path
    serial: str | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass(frozen=True)
class M1RunOutcome:
    success: bool
    run_dir: PathRef
    trace_path: PathRef
    summary_path: PathRef
    request_paths: tuple[PathRef, ...]
    response_paths: tuple[PathRef, ...]
    tool_call_paths: tuple[PathRef, ...]
    tool_result_paths: tuple[PathRef, ...]
    backend_result_path: PathRef | None
    backend_stdout_path: PathRef | None
    backend_stderr_path: PathRef | None
    backend_exit_code: int | None
    final_text: str
    run_conclusion: str
    blocker: str | None = None


def run_m1(config: M1RunConfig) -> M1RunOutcome:
    artifacts = ArtifactStore(config.output_dir, run_prefix="m1")
    shell_tool = ShellExecTool(serial=config.serial)
    registry = ToolRegistry([shell_tool])
    client = OpenAICompatClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": config.user_input},
    ]
    request_paths: list[PathRef] = []
    response_paths: list[PathRef] = []
    tool_call_paths: list[PathRef] = []
    tool_result_paths: list[PathRef] = []
    backend_result_path: PathRef | None = None
    backend_stdout_path: PathRef | None = None
    backend_stderr_path: PathRef | None = None
    backend_exit_code: int | None = None
    last_tool_result: ToolResult | None = None

    artifacts.append_trace(
        "run_started",
        {
            "model": config.model,
            "base_url": config.base_url,
            "serial": config.serial,
            "user_input": config.user_input,
            "tools": [tool["function"]["name"] for tool in registry.to_openai_tools()],
        },
    )

    try:
        first_request_path, first_response_path, first_payload = _call_model(
            client=client,
            artifacts=artifacts,
            turn_index=1,
            messages=messages,
            tools=registry.to_openai_tools(),
            tool_choice=None,
        )
    except ModelClientError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker=str(exc),
        )

    request_paths.append(first_request_path)
    response_paths.append(first_response_path)

    try:
        assistant_message = _first_choice_message(first_payload)
    except ValueError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
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

    if len(tool_calls) != 1:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker=f"expected exactly one tool_call on turn 1, got {len(tool_calls)}",
        )

    messages.append(
        {
            "role": "assistant",
            "content": assistant_message.get("content"),
            "tool_calls": tool_calls,
        }
    )

    tool_call = tool_calls[0]
    tool_name = ((tool_call.get("function") or {}).get("name")) or "shell_exec"
    tool_call_path = artifacts.write_json(f"tool_call_01_{tool_name}.json", tool_call)
    tool_call_paths.append(tool_call_path)

    try:
        arguments, tool_result = registry.execute_tool_call(tool_call)
    except Exception as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker=f"tool execution failed for {tool_name}: {exc}",
        )

    last_tool_result = tool_result
    artifacts.append_trace(
        "tool_call",
        {
            "index": 1,
            "name": tool_name,
            "arguments": arguments,
            "path": tool_call_path.path,
        },
    )

    tool_result_payload = tool_result.to_dict()
    tool_result_path = artifacts.write_json(f"tool_result_01_{tool_name}.json", tool_result_payload)
    tool_result_paths.append(tool_result_path)
    artifacts.append_trace(
        "tool_result",
        {
            "index": 1,
            "name": tool_name,
            "result": tool_result_payload,
            "path": tool_result_path.path,
        },
    )

    backend_result = shell_tool.last_backend_result
    if backend_result is None:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker="shell_exec did not record a backend result",
        )

    backend_result_path, backend_stdout_path, backend_stderr_path = _write_backend_result(
        artifacts=artifacts,
        name=tool_name,
        result=backend_result,
    )
    backend_exit_code = backend_result.exit_code

    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "name": tool_name,
            "content": json.dumps(tool_result_payload, ensure_ascii=False),
        }
    )

    try:
        second_request_path, second_response_path, second_payload = _call_model(
            client=client,
            artifacts=artifacts,
            turn_index=2,
            messages=messages,
            tools=None,
            tool_choice=None,
        )
    except ModelClientError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker=str(exc),
        )

    request_paths.append(second_request_path)
    response_paths.append(second_response_path)

    try:
        final_message = _first_choice_message(second_payload)
    except ValueError as exc:
        return _failure_outcome(
            artifacts=artifacts,
            request_paths=request_paths,
            response_paths=response_paths,
            tool_call_paths=tool_call_paths,
            tool_result_paths=tool_result_paths,
            backend_result_path=backend_result_path,
            backend_stdout_path=backend_stdout_path,
            backend_stderr_path=backend_stderr_path,
            backend_exit_code=backend_exit_code,
            blocker=str(exc),
        )

    final_text = (final_message.get("content") or "").strip()
    final_tool_calls = final_message.get("tool_calls") or []
    artifacts.append_trace(
        "assistant_response",
        {
            "turn": 2,
            "content": final_text,
            "tool_call_count": len(final_tool_calls),
        },
    )

    success = bool(final_text) and not final_tool_calls and bool(last_tool_result and last_tool_result.ok)
    run_conclusion = (
        "M1 single-tool loop completed a real roundtrip on the current OpenAI-compatible model endpoint: user input reached the model, shell_exec tool_call was emitted, backend execution succeeded, tool result was fed back, and the model produced a final answer."
        if success
        else "M1 single-tool loop did not complete the full roundtrip."
    )
    blocker = None
    if not success:
        if final_tool_calls:
            blocker = "model attempted an unexpected second tool call on turn 2"
        elif not final_text:
            blocker = "model did not produce a final text response after tool result feedback"
        elif last_tool_result and not last_tool_result.ok:
            blocker = f"shell_exec returned exit_code={last_tool_result.exit_code}"
        else:
            blocker = "unknown M1 failure"

    summary_payload = {
        "success": success,
        "model": config.model,
        "base_url": config.base_url,
        "serial": config.serial,
        "user_input": config.user_input,
        "request_paths": [ref.to_dict() for ref in request_paths],
        "response_paths": [ref.to_dict() for ref in response_paths],
        "tool_call_paths": [ref.to_dict() for ref in tool_call_paths],
        "tool_result_paths": [ref.to_dict() for ref in tool_result_paths],
        "backend_result_path": None if backend_result_path is None else backend_result_path.to_dict(),
        "backend_stdout_path": None if backend_stdout_path is None else backend_stdout_path.to_dict(),
        "backend_stderr_path": None if backend_stderr_path is None else backend_stderr_path.to_dict(),
        "backend_exit_code": backend_exit_code,
        "final_text": final_text,
        "run_conclusion": run_conclusion,
        "blocker": blocker,
    }
    summary_path = artifacts.write_json("summary.json", summary_payload)
    artifacts.append_trace(
        "run_finished",
        {
            "success": success,
            "summary_path": summary_path.path,
            "final_text": final_text,
            "run_conclusion": run_conclusion,
            "blocker": blocker,
        },
    )

    paths = artifacts.paths()
    return M1RunOutcome(
        success=success,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        summary_path=summary_path,
        request_paths=tuple(request_paths),
        response_paths=tuple(response_paths),
        tool_call_paths=tuple(tool_call_paths),
        tool_result_paths=tuple(tool_result_paths),
        backend_result_path=backend_result_path,
        backend_stdout_path=backend_stdout_path,
        backend_stderr_path=backend_stderr_path,
        backend_exit_code=backend_exit_code,
        final_text=final_text,
        run_conclusion=run_conclusion,
        blocker=blocker,
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

    request_path = artifacts.write_json(f"request_{turn_index:02d}.json", result.request_payload)
    response_path = artifacts.write_json(f"response_{turn_index:02d}.json", result.response_payload)
    artifacts.append_trace(
        "model_io",
        {
            "turn": turn_index,
            "request_path": request_path.path,
            "response_path": response_path.path,
        },
    )
    return request_path, response_path, result.response_payload


def _first_choice_message(response_payload: dict[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices") or []
    if not choices:
        raise ValueError("model response contained no choices")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("model response choice[0].message is missing")
    return message


def _write_backend_result(
    *,
    artifacts: ArtifactStore,
    name: str,
    result: BackendCommandResult,
) -> tuple[PathRef, PathRef, PathRef]:
    stdout_path = artifacts.write_text(f"backend_01_{name}.stdout.txt", result.stdout)
    stderr_path = artifacts.write_text(f"backend_01_{name}.stderr.txt", result.stderr)
    payload = result.to_dict()
    payload["stdout_path"] = stdout_path.to_dict()
    payload["stderr_path"] = stderr_path.to_dict()
    result_path = artifacts.write_json(f"backend_01_{name}.result.json", payload)
    artifacts.append_trace(
        "backend_result",
        {
            "name": name,
            "exit_code": result.exit_code,
            "result_path": result_path.path,
            "stdout_path": stdout_path.path,
            "stderr_path": stderr_path.path,
        },
    )
    return result_path, stdout_path, stderr_path


def _failure_outcome(
    *,
    artifacts: ArtifactStore,
    request_paths: list[PathRef],
    response_paths: list[PathRef],
    tool_call_paths: list[PathRef],
    tool_result_paths: list[PathRef],
    backend_result_path: PathRef | None,
    backend_stdout_path: PathRef | None,
    backend_stderr_path: PathRef | None,
    backend_exit_code: int | None,
    blocker: str,
) -> M1RunOutcome:
    summary_payload = {
        "success": False,
        "request_paths": [ref.to_dict() for ref in request_paths],
        "response_paths": [ref.to_dict() for ref in response_paths],
        "tool_call_paths": [ref.to_dict() for ref in tool_call_paths],
        "tool_result_paths": [ref.to_dict() for ref in tool_result_paths],
        "backend_result_path": None if backend_result_path is None else backend_result_path.to_dict(),
        "backend_stdout_path": None if backend_stdout_path is None else backend_stdout_path.to_dict(),
        "backend_stderr_path": None if backend_stderr_path is None else backend_stderr_path.to_dict(),
        "backend_exit_code": backend_exit_code,
        "run_conclusion": "M1 single-tool loop did not complete the full roundtrip.",
        "blocker": blocker,
    }
    summary_path = artifacts.write_json("summary.json", summary_payload)
    artifacts.append_trace(
        "run_finished",
        {
            "success": False,
            "summary_path": summary_path.path,
            "run_conclusion": "M1 single-tool loop did not complete the full roundtrip.",
            "blocker": blocker,
        },
    )
    paths = artifacts.paths()
    return M1RunOutcome(
        success=False,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        summary_path=summary_path,
        request_paths=tuple(request_paths),
        response_paths=tuple(response_paths),
        tool_call_paths=tuple(tool_call_paths),
        tool_result_paths=tuple(tool_result_paths),
        backend_result_path=backend_result_path,
        backend_stdout_path=backend_stdout_path,
        backend_stderr_path=backend_stderr_path,
        backend_exit_code=backend_exit_code,
        final_text="",
        run_conclusion="M1 single-tool loop did not complete the full roundtrip.",
        blocker=blocker,
    )
