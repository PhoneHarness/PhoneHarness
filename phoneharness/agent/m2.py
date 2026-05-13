from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..backend import BackendCommandResult
from ..model.client import ModelClientError, OpenAICompatClient
from ..tools.registry import ToolRegistry
from ..util.trace import ArtifactStore
from .message import ArtifactRecord, ErrorType, PathLayer, PathRef, RunRecord, StepRecord, ToolCallRecord, ToolResult


DEFAULT_SYSTEM_PROMPT = (
    "You are the PhoneHarness M2 CLI controller. "
    "Stay on the CLI/file-processing path only; do not invent GUI steps. "
    "Prefer task-first tools over generic execution. Use task_health_check for environment checks, "
    "task_pdf_merge for PDF merging, and task_word_append for Word editing. "
    "Use file_transfer only when a file must cross layers, use python_exec for targeted Python execution inside Ubuntu, "
    "and use shell_exec only as fallback or debug. "
    "For file tools, absolute host paths are host PathRef values, /data/data/com.termux/files/home/... paths are termux PathRef values, "
    "and /root/... paths are ubuntu PathRef values. "
    "Whenever you call a tool, rely on its structured result fields ok, summary, checks, and artifact_paths. "
    "Do not call more tools than needed. After the required tools complete, answer briefly in Chinese with the outcome and key verification signals."
)


@dataclass(frozen=True)
class M2RunConfig:
    user_input: str
    base_url: str
    api_key: str
    model: str
    output_dir: str | Path
    tool_registry: ToolRegistry
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_steps: int = 8
    active_skills: tuple[str, ...] = ()
    run_prefix: str = "m2"
    artifact_layer: PathLayer = "host"


@dataclass(frozen=True)
class M2RunOutcome:
    success: bool
    run_dir: PathRef
    trace_path: PathRef
    summary_path: PathRef
    request_paths: tuple[PathRef, ...]
    response_paths: tuple[PathRef, ...]
    tool_call_paths: tuple[PathRef, ...]
    tool_result_paths: tuple[PathRef, ...]
    final_text: str
    run_conclusion: str
    run_record: RunRecord
    error_type: ErrorType | None = None
    blocker: str | None = None


def run_m2(config: M2RunConfig) -> M2RunOutcome:
    if not config.tool_registry.to_openai_tools():
        raise ValueError("M2 requires a non-empty tool registry")

    artifacts = ArtifactStore(
        config.output_dir,
        run_prefix=config.run_prefix,
        path_layer=config.artifact_layer,
    )
    client = OpenAICompatClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )
    paths = artifacts.paths()
    run_record = RunRecord(
        id=Path(paths.run_dir.path).name,
        user_input=config.user_input,
        status="running",
        control_provider=_provider_label(config.base_url),
        control_model=config.model,
        active_skills=list(config.active_skills),
    )

    request_paths: list[PathRef] = []
    response_paths: list[PathRef] = []
    tool_call_paths: list[PathRef] = []
    tool_result_paths: list[PathRef] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": config.user_input},
    ]

    artifacts.append_trace(
        "run_started",
        {
            "run_id": run_record.id,
            "model": config.model,
            "base_url": config.base_url,
            "tools": [tool["function"]["name"] for tool in config.tool_registry.to_openai_tools()],
            "user_input": config.user_input,
            "active_skills": list(config.active_skills),
        },
    )

    final_text = ""
    blocker: str | None = None
    error_type: ErrorType | None = None

    for step_index in range(1, config.max_steps + 1):
        step_record = StepRecord(
            run_id=run_record.id,
            index=step_index,
            step_type="cli",
            decided_by=config.model,
        )
        run_record.steps.append(step_record)

        try:
            request_path, response_path, response_payload = _call_model(
                client=client,
                artifacts=artifacts,
                turn_index=step_index,
                messages=messages,
                tools=config.tool_registry.to_openai_tools(),
            )
        except ModelClientError as exc:
            blocker = str(exc)
            error_type = _classify_model_error(exc)
            break

        request_paths.append(request_path)
        response_paths.append(response_path)
        step_record.request_path = request_path
        step_record.response_path = response_path

        try:
            assistant_message = _first_choice_message(response_payload)
        except ValueError as exc:
            blocker = str(exc)
            error_type = "model_error"
            break

        assistant_content = assistant_message.get("content")
        step_record.assistant_text = assistant_content if isinstance(assistant_content, str) else ""
        tool_calls = assistant_message.get("tool_calls") or []
        artifacts.append_trace(
            "assistant_response",
            {
                "turn": step_index,
                "content": assistant_message.get("content"),
                "tool_call_count": len(tool_calls),
            },
        )

        if not tool_calls:
            final_text = (assistant_message.get("content") or "").strip()
            break

        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.get("content"),
                "tool_calls": tool_calls,
            }
        )

        for call_index, tool_call in enumerate(tool_calls, start=1):
            tool_name = ((tool_call.get("function") or {}).get("name")) or f"tool_{call_index}"
            tool_call_path = artifacts.write_json(
                f"tool_call_{step_index:02d}_{call_index:02d}_{tool_name}.json",
                tool_call,
            )
            tool_call_paths.append(tool_call_path)

            try:
                arguments, tool_result, backend_runs, tool_error_type = config.tool_registry.execute_tool_call_detailed(tool_call)
            except Exception as exc:
                blocker = f"tool execution failed for {tool_name}: {exc}"
                error_type = _classify_tool_error(exc)
                step_record.tool_calls.append(
                    ToolCallRecord(
                        name=tool_name,
                        args=_parse_tool_args(tool_call),
                        status="failed",
                        result_summary=str(exc),
                        result_ok=False,
                        error_type=error_type,
                        tool_call_path=tool_call_path,
                    )
                )
                break

            tool_result_payload = tool_result.to_dict()
            tool_result_path = artifacts.write_json(
                f"tool_result_{step_index:02d}_{call_index:02d}_{tool_name}.json",
                tool_result_payload,
            )
            tool_result_paths.append(tool_result_path)
            step_record.tool_calls.append(
                ToolCallRecord(
                    name=tool_name,
                    args=arguments,
                    status="completed" if tool_result.ok else "failed",
                    result_summary=tool_result.summary,
                    result_ok=tool_result.ok,
                    error_type=None if tool_result.ok else (tool_error_type or "tool_error"),
                    tool_call_path=tool_call_path,
                    tool_result_path=tool_result_path,
                )
            )
            _register_tool_artifacts(run_record, tool_result, step_index)
            _write_backend_runs(
                artifacts=artifacts,
                run_record=run_record,
                step_index=step_index,
                call_index=call_index,
                tool_name=tool_name,
                backend_runs=backend_runs,
            )

            artifacts.append_trace(
                "tool_call",
                {
                    "step": step_index,
                    "index": call_index,
                    "name": tool_name,
                    "arguments": arguments,
                    "path": tool_call_path.path,
                },
            )
            artifacts.append_trace(
                "tool_result",
                {
                    "step": step_index,
                    "index": call_index,
                    "name": tool_name,
                    "result": tool_result_payload,
                    "path": tool_result_path.path,
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

            if not tool_result.ok and error_type is None:
                error_type = tool_error_type or "tool_error"
                blocker = blocker or tool_result.summary

        if blocker is not None:
            break

    if blocker is None and not final_text:
        blocker = f"model did not finish within max_steps={config.max_steps}"
        error_type = "timeout"

    success = blocker is None and bool(final_text)
    run_record.status = "completed" if success else "failed"
    run_record.final_text = final_text
    run_record.error_type = error_type
    run_conclusion = (
        "M2 multi-tool CLI harness completed on the current OpenAI-compatible model endpoint."
        if success
        else "M2 multi-tool CLI harness did not complete successfully."
    )

    summary_payload = {
        "success": success,
        "run": run_record.to_dict(),
        "request_paths": [ref.to_dict() for ref in request_paths],
        "response_paths": [ref.to_dict() for ref in response_paths],
        "tool_call_paths": [ref.to_dict() for ref in tool_call_paths],
        "tool_result_paths": [ref.to_dict() for ref in tool_result_paths],
        "trace_path": paths.trace.to_dict(),
        "final_text": final_text,
        "run_conclusion": run_conclusion,
        "error_type": error_type,
        "blocker": blocker,
    }
    summary_path = artifacts.write_json("summary.json", summary_payload)
    summary_payload["summary_path"] = summary_path.to_dict()
    summary_path = artifacts.write_json("summary.json", summary_payload)
    artifacts.append_trace(
        "run_finished",
        {
            "success": success,
            "summary_path": summary_path.path,
            "final_text": final_text,
            "error_type": error_type,
            "blocker": blocker,
        },
    )

    return M2RunOutcome(
        success=success,
        run_dir=paths.run_dir,
        trace_path=paths.trace,
        summary_path=summary_path,
        request_paths=tuple(request_paths),
        response_paths=tuple(response_paths),
        tool_call_paths=tuple(tool_call_paths),
        tool_result_paths=tuple(tool_result_paths),
        final_text=final_text,
        run_conclusion=run_conclusion,
        run_record=run_record,
        error_type=error_type,
        blocker=blocker,
    )


def _call_model(
    *,
    client: OpenAICompatClient,
    artifacts: ArtifactStore,
    turn_index: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> tuple[PathRef, PathRef, dict[str, Any]]:
    try:
        result = client.chat_completion(messages=messages, tools=tools, tool_choice=None)
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


def _register_tool_artifacts(run_record: RunRecord, tool_result: ToolResult, step_index: int) -> None:
    for path_ref in tool_result.artifact_paths:
        run_record.artifacts.append(
            ArtifactRecord(
                path_ref=path_ref,
                type="tool_artifact",
                source_step=step_index,
            )
        )


def _write_backend_runs(
    *,
    artifacts: ArtifactStore,
    run_record: RunRecord,
    step_index: int,
    call_index: int,
    tool_name: str,
    backend_runs: list[tuple[str, BackendCommandResult]],
) -> None:
    for backend_index, (label, result) in enumerate(backend_runs, start=1):
        stem = f"backend_{step_index:02d}_{call_index:02d}_{backend_index:02d}_{tool_name}_{label}"
        stdout_path = artifacts.write_text(f"{stem}.stdout.txt", result.stdout)
        stderr_path = artifacts.write_text(f"{stem}.stderr.txt", result.stderr)
        payload = result.to_dict()
        payload["stdout_path"] = stdout_path.to_dict()
        payload["stderr_path"] = stderr_path.to_dict()
        result_path = artifacts.write_json(f"{stem}.result.json", payload)
        run_record.artifacts.extend(
            [
                ArtifactRecord(path_ref=result_path, type="backend_result", source_step=step_index),
                ArtifactRecord(path_ref=stdout_path, type="backend_stdout", source_step=step_index),
                ArtifactRecord(path_ref=stderr_path, type="backend_stderr", source_step=step_index),
            ]
        )
        artifacts.append_trace(
            "backend_result",
            {
                "step": step_index,
                "index": call_index,
                "backend_index": backend_index,
                "tool_name": tool_name,
                "label": label,
                "exit_code": result.exit_code,
                "result_path": result_path.path,
                "stdout_path": stdout_path.path,
                "stderr_path": stderr_path.path,
            },
        )


def _parse_tool_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    arguments_text = ((tool_call.get("function") or {}).get("arguments")) or "{}"
    try:
        parsed = json.loads(arguments_text)
    except Exception:
        return {"_raw": arguments_text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _classify_model_error(exc: Exception) -> ErrorType:
    message = str(exc).lower()
    if "timeout" in message:
        return "timeout"
    return "model_error"


def _classify_tool_error(exc: Exception) -> ErrorType:
    message = str(exc).lower()
    if "timeout" in message:
        return "timeout"
    if "adb" in message or "backend" in message or "exit_code" in message:
        return "backend_error"
    return "tool_error"


def _provider_label(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.netloc or base_url


__all__ = ["M2RunConfig", "M2RunOutcome", "run_m2"]
