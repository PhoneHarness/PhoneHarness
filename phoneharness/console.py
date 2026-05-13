"""PhoneHarness CLI-first Agent Console.

The default user entry point for the PhoneHarness CLI+GUI mixed orchestration framework.
Runs as an interactive REPL in Termux, showing live execution progress.

Usage:
    python3 -m phoneharness console [--model doubao-2.0-pro] [--base-url ...]

Design principles:
    - Claude Code style: visible execution, not chat bubbles
    - Single model unified CLI+GUI orchestration
    - Step-level progress visibility
    - Framework entry point, not a demo shell
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent.message import PathRef, ToolResult
from .backend import AdbBackend, BackendCommandResult, LocalBackend
from .model.client import ModelClientError, OpenAICompatClient
from .skills import inject_skill_prompt, load_skill_files
from .tools.file_transfer import FileTransferTool
from .tools.gui import GUIActionTool, GUIInteractTool, KeyeventTool, ScreenshotTool, SwipeTool, TapTool, TypeTextTool, UiDumpTool
from .tools.seed_gui_subtask import SeedGUISubtaskTool
from .tools.python_exec import PythonExecTool
from .tools.registry import ToolRegistry
from .tools.load_skill import LoadSkillTool
from .tools.shell_exec import ShellExecTool
from .tools.task_health_check import TaskHealthCheckTool
from .tools.task_pdf_merge import TaskPdfMergeTool
from .tools.task_word_append import TaskWordAppendTool
from .util.trace import ArtifactStore


# ── ANSI colors ──
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"
BG_BLUE = "\033[44m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


@dataclass
class ConsoleConfig:
    model: str = "doubao-2.0-pro"
    base_url: str = "http://10.0.2.2:8918/v1"
    api_key: str = "test"
    gui_proxy_url: str = "http://10.0.2.2:8919"
    gui_model: str | None = None      # Required. Inner GUI worker model id.
    gui_api_url: str | None = None     # Optional. Defaults to base_url.
    gui_api_key: str | None = None     # Optional. Defaults to api_key.
    gui_mode: str = "delegated"        # "delegated" (seed_gui black-box) or "flat" (direct gui_* tools)
    flat_gui_protocol: str = "tool_call"  # "tool_call" (default) or "text_xml" (Seed XML sub-loop)
    serial: str | None = None
    skill_paths: tuple[str, ...] = ()
    max_steps: int = 25
    output_dir: str = "artifacts/console"


def _resolve_gui_backend(config: ConsoleConfig) -> tuple[str, str, str]:
    gui_model = (config.gui_model or "").strip()
    if not gui_model:
        raise ValueError("GUI model must be explicitly specified. Pass --gui-model <model>.")

    gui_api_url = (config.gui_api_url or config.base_url).strip()
    if not gui_api_url:
        raise ValueError("GUI API URL is empty. Pass --gui-api-url or --base-url.")

    gui_api_key = (config.gui_api_key or config.api_key).strip()
    if not gui_api_key:
        raise ValueError("GUI API key is empty. Pass --gui-api-key or --api-key.")

    return gui_model, gui_api_url, gui_api_key


def _print_banner(config: ConsoleConfig) -> None:
    """Print the console banner."""
    gui_model, gui_api_url, _ = _resolve_gui_backend(config)
    print()
    print(f"  {_c(BOLD + BLUE, '╔══════════════════════════════════════════════════╗')}")
    print(f"  {_c(BOLD + BLUE, '║')}  {_c(BOLD + WHITE, 'PhoneHarness Agent Console')}                     {_c(BOLD + BLUE, '║')}")
    print(f"  {_c(BOLD + BLUE, '║')}  {_c(DIM, 'CLI + GUI Mixed Orchestration Framework')}         {_c(BOLD + BLUE, '║')}")
    print(f"  {_c(BOLD + BLUE, '╚══════════════════════════════════════════════════╝')}")
    print()
    print(f"  {_c(DIM, 'Model:')}  {_c(CYAN, config.model)}")
    print(f"  {_c(DIM, 'GUI Model:')} {_c(CYAN, gui_model)}")
    print(f"  {_c(DIM, 'GUI Mode:')}  {_c(DIM, config.gui_mode)}")
    print(f"  {_c(DIM, 'API:')}    {_c(DIM, config.base_url)}")
    print(f"  {_c(DIM, 'GUI API:')} {_c(DIM, gui_api_url)}")
    if config.skill_paths:
        print(f"  {_c(DIM, 'Skills:')} {_c(YELLOW, ', '.join(Path(p).stem for p in config.skill_paths))}")
    print()
    print(f"  {_c(DIM, 'Type a task in natural language. The agent will use CLI and GUI tools.')}")
    print(f"  {_c(DIM, 'Commands: /quit /clear /model <name> /status')}")
    print()


def _print_step_header(step: int) -> None:
    print(f"\n  {_c(BOLD + BLUE, f'── Step {step} ──')}")


def _print_tool_call(name: str, args: dict) -> None:
    args_short = json.dumps(args, ensure_ascii=False)
    if len(args_short) > 120:
        args_short = args_short[:117] + "..."
    icon = "🔧" if not name.startswith("gui_") else "📱"
    print(f"  {icon} {_c(YELLOW, name)}({_c(DIM, args_short)})")


def _print_tool_result(name: str, result: ToolResult) -> None:
    if result.ok:
        status = _c(GREEN, "✅")
        summary = result.summary[:100] if result.summary else "ok"
    else:
        status = _c(RED, "❌")
        summary = result.summary[:100] if result.summary else "failed"
    print(f"  {status} {_c(DIM, summary)}")


def _print_assistant_text(text: str) -> None:
    if text:
        # Indent multi-line text
        lines = text.strip().split("\n")
        for line in lines:
            print(f"  {_c(WHITE, line)}")


def _print_final(success: bool, text: str, duration: float) -> None:
    print()
    if success:
        print(f"  {_c(BOLD + GREEN, '━━━ Done ━━━')} {_c(DIM, f'({duration:.1f}s)')}")
    else:
        print(f"  {_c(BOLD + RED, '━━━ Failed ━━━')} {_c(DIM, f'({duration:.1f}s)')}")
    if text:
        print()
        _print_assistant_text(text)
    print()


def _build_registry(config: ConsoleConfig) -> tuple[Any, ToolRegistry, str | None]:
    """Build backend and tool registry."""
    gui_model, gui_api_url, gui_api_key = _resolve_gui_backend(config)

    if config.serial:
        backend = AdbBackend()
        serial = config.serial
    else:
        backend = LocalBackend(workdir=Path.cwd(), use_proot=False)
        serial = None

    from .tools.mobile_actions import ALL_MOBILE_ACTION_TOOLS

    mobile_action_tools = [cls(backend=backend, serial=serial) for cls in ALL_MOBILE_ACTION_TOOLS]

    # Load MCP-Bench tools (CLI + Skill wrappers)
    try:
        from .tools.mcp_bench import load_mcp_bench_tools
        mcp_bench_tools = load_mcp_bench_tools()
    except Exception:
        mcp_bench_tools = []

    # Load GUI MCP-style aliases
    try:
        from .tools.gui_mcp_aliases import load_gui_mcp_aliases
        gui_mcp_tools = load_gui_mcp_aliases(config.gui_proxy_url)
    except Exception:
        gui_mcp_tools = []

    registry = ToolRegistry([
        # Mobile action tools (high-priority semantic tools for device actions)
        *mobile_action_tools,
        # CLI tools
        ShellExecTool(backend=backend, serial=serial),
        PythonExecTool(backend=backend, serial=serial),
        TaskHealthCheckTool(backend=backend, serial=serial),
        TaskPdfMergeTool(backend=backend, serial=serial),
        TaskWordAppendTool(backend=backend, serial=serial),
        FileTransferTool(backend=backend, serial=serial),
        # Skill loading (progressive disclosure)
        LoadSkillTool(),
        # GUI tools — mode-aware registration
        *(
            # delegated: register seed_gui black-box + low-level gui_*
            [SeedGUISubtaskTool(gui_proxy_url=config.gui_proxy_url,
                                gui_model=gui_model,
                                gui_api_url=gui_api_url,
                                gui_api_key=gui_api_key)]
            if config.gui_mode == "delegated"
            # flat: no seed_gui, model uses low-level gui_* directly
            else []
        ),
        # Flat mode GUI tools — depends on protocol
        *(
            # flat + text_xml: gui_interact (text_xml sub-loop, uses gui_model for GUI)
            [GUIInteractTool(gui_proxy_url=config.gui_proxy_url,
                             model=gui_model,
                             api_url=gui_api_url,
                             api_key=gui_api_key)]
            if config.gui_mode == "flat" and config.flat_gui_protocol == "text_xml"
            # flat + tool_call: gui_action (model uses tool_call with native format)
            else [GUIActionTool(gui_proxy_url=config.gui_proxy_url,
                                model_name=config.model)]
            if config.gui_mode == "flat"
            else []
        ),
        # Low-level gui_* tools (both modes, normalized coords in flat mode)
        ScreenshotTool(gui_proxy_url=config.gui_proxy_url,
                       normalized_coords=(config.gui_mode == "flat")),
        TapTool(gui_proxy_url=config.gui_proxy_url,
                normalized_coords=(config.gui_mode == "flat")),
        SwipeTool(gui_proxy_url=config.gui_proxy_url,
                  normalized_coords=(config.gui_mode == "flat")),
        TypeTextTool(gui_proxy_url=config.gui_proxy_url),
        KeyeventTool(gui_proxy_url=config.gui_proxy_url),
        UiDumpTool(gui_proxy_url=config.gui_proxy_url),
        # GUI MCP-style aliases (mcp__android_gui__*)
        *gui_mcp_tools,
        # MCP-Bench tools (termux-api CLI + Python skills)
        *mcp_bench_tools,
    ])
    return backend, registry, serial


def _get_system_prompt(config: ConsoleConfig) -> str:
    from .agent.m4 import DEFAULT_M4_SYSTEM_PROMPT, FLAT_MODE_GUI_SECTION, DELEGATED_MODE_GUI_SECTION
    # Compose mode-aware SP: base + mode-specific GUI section
    if config.gui_mode == "flat":
        base = DEFAULT_M4_SYSTEM_PROMPT + "\n\n" + FLAT_MODE_GUI_SECTION
    else:
        base = DEFAULT_M4_SYSTEM_PROMPT + "\n\n" + DELEGATED_MODE_GUI_SECTION
    skills = load_skill_files(config.skill_paths)
    return inject_skill_prompt(base, skills)


def _prune_old_images(messages: list[dict[str, Any]], keep_last: int = 1) -> None:
    """Remove old base64 images from tool messages to prevent token overflow.

    Walks messages in reverse, keeps the *keep_last* most recent images intact,
    and replaces older image_url entries with a short text note.
    """
    seen = 0
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        has_image = any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content
        )
        if not has_image:
            continue
        seen += 1
        if seen > keep_last:
            msg["content"] = [
                part if part.get("type") != "image_url"
                else {"type": "text", "text": "[screenshot image removed to save context]"}
                for part in content
            ]


def run_console_turn_evented(
    user_input: str,
    config: ConsoleConfig,
    client: OpenAICompatClient,
    registry: ToolRegistry,
    system_prompt: str,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> bool:
    """Execute one user turn, emitting structured events via *on_event*.

    Events are dicts with an ``"event"`` key:
      - ``{"event": "start", "user_input": ...}``
      - ``{"event": "step", "step": N}``
      - ``{"event": "thinking"}``
      - ``{"event": "assistant_text", "text": ...}``
      - ``{"event": "tool_call", "step": N, "name": ..., "arguments": ...}``
      - ``{"event": "tool_result", "step": N, "name": ..., "ok": bool, "summary": ...}``
      - ``{"event": "done", "success": bool, "text": ..., "duration": float}``
      - ``{"event": "error", "message": ...}``

    When *on_event* is ``None`` (the default), the function falls back to the
    original print-based console output — no behaviour change for the REPL.
    """
    emit = on_event or (lambda _ev: None)
    use_print = on_event is None  # legacy print path

    start_time = time.time()
    if history is not None:
        # Multi-turn: append new user message to existing conversation
        messages = history
        messages.append({"role": "user", "content": user_input})
    else:
        # Single-turn: fresh conversation
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

    emit({"event": "start", "user_input": user_input})
    if use_print:
        print(f"\n  {_c(BOLD, '▶')} {_c(WHITE, user_input)}")

    tools_schema = registry.to_openai_tools()
    final_text = ""
    success = False

    for step in range(1, config.max_steps + 1):
        step_start = time.time()
        emit({"event": "step", "step": step})
        if use_print:
            _print_step_header(step)

        # Prune old images from conversation to avoid token overflow.
        # Keep last 2 images (balance context vs token limit).
        _prune_old_images(messages, keep_last=2)

        # Call model
        emit({"event": "thinking"})
        if use_print:
            print(f"  {_c(DIM, '⏳ Thinking...')}", end="", flush=True)
        llm_start = time.time()
        try:
            result = client.chat_completion(
                messages=messages,
                tools=tools_schema if tools_schema else None,
                max_tokens=1024,
            )
        except ModelClientError as exc:
            emit({"event": "error", "message": str(exc)})
            if use_print:
                print(f"\r  {_c(RED, f'⚠ Model error: {exc}')}")
                _print_final(False, "", time.time() - start_time)
            else:
                emit({"event": "done", "success": False, "text": "", "duration": time.time() - start_time})
            return False
        llm_ms = round((time.time() - llm_start) * 1000)

        response = result.response_payload
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Extract token usage
        usage = response.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)

        # Emit reasoning content if present (check multiple field names for compatibility)
        reasoning = (message.get("reasoning_content")
                     or message.get("reasoning")
                     or "")
        if isinstance(reasoning, list):
            # Some models return reasoning_details as list of dicts
            reasoning = "\n".join(
                item.get("text", "") for item in reasoning
                if isinstance(item, dict) and item.get("text")
            )
        if reasoning:
            emit({"event": "reasoning", "content": reasoning, "step": step})
        if use_print:
            print(f"\r  {_c(DIM, '💭 Model responded')}" + " " * 20)

        content = message.get("content", "")
        if content:
            emit({"event": "assistant_text", "text": content})
            if use_print:
                _print_assistant_text(content)

        tool_calls = message.get("tool_calls", [])

        if not tool_calls:
            final_text = content
            success = True
            break

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        # Flat-mode safety: if this turn has gui_screenshot AND gui_tap/swipe/type,
        # block the action tools (model must observe first, act next turn)
        _GUI_ACTION_TOOLS = {"gui_tap", "gui_swipe", "gui_type", "gui_keyevent", "gui_action"}
        _flat_has_screenshot = False
        _flat_mode = config.gui_mode == "flat"

        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "?")
            try:
                tool_args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                tool_args = {"_raw": func.get("arguments", "")}

            # Flat-mode guard: block GUI actions after screenshot in same turn
            if _flat_mode and tool_name == "gui_screenshot":
                _flat_has_screenshot = True
            if _flat_mode and _flat_has_screenshot and tool_name in _GUI_ACTION_TOOLS:
                tool_result = ToolResult(
                    ok=False,
                    output="BLOCKED: In flat GUI mode, you must observe the screenshot first, "
                           "then act in the NEXT turn. Do not combine gui_screenshot with "
                           "gui_tap/swipe/type in the same turn.",
                    summary="flat-mode: observe first, act next turn",
                )
                emit({"event": "tool_call", "step": step, "name": tool_name, "arguments": tool_args})
                if use_print:
                    _print_tool_call(tool_name, tool_args)
                action_ms = 0
                total_ms = round((time.time() - step_start) * 1000)
                _tr_event = {
                    "event": "tool_result", "step": step, "name": tool_name,
                    "ok": False, "summary": tool_result.summary, "output": tool_result.output,
                    "timing_ms": {"llm": llm_ms, "action": 0, "total": total_ms},
                    "tokens": {"prompt": tokens_in, "completion": tokens_out},
                }
                emit(_tr_event)
                if use_print:
                    _print_tool_result(tool_name, tool_result)
                messages.append({
                    "role": "tool", "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                    "content": json.dumps(tool_result.to_dict(), ensure_ascii=False),
                })
                continue

            emit({"event": "tool_call", "step": step, "name": tool_name, "arguments": tool_args})
            if use_print:
                _print_tool_call(tool_name, tool_args)

            action_start = time.time()
            tool_obj = registry.get(tool_name)
            if tool_obj is None:
                tool_result = ToolResult(ok=False, output=f"Unknown tool: {tool_name}", summary=f"Unknown tool: {tool_name}")
            else:
                try:
                    tool_result = tool_obj.execute(tool_args)
                except Exception as exc:
                    tool_result = ToolResult(ok=False, output=str(exc), summary=f"Error: {exc}")
            action_ms = round((time.time() - action_start) * 1000)
            total_ms = round((time.time() - step_start) * 1000)

            _tr_event: dict[str, Any] = {
                "event": "tool_result", "step": step, "name": tool_name,
                "ok": tool_result.ok, "summary": tool_result.summary or "", "output": tool_result.output,
                "timing_ms": {"llm": llm_ms, "action": action_ms, "total": total_ms},
                "tokens": {"prompt": tokens_in, "completion": tokens_out},
            }
            if tool_result.intent is not None:
                _tr_event["intent"] = tool_result.intent
            if tool_result.artifact_paths:
                _tr_event["artifact_paths"] = [p.to_dict() for p in tool_result.artifact_paths]
            emit(_tr_event)
            if use_print:
                _print_tool_result(tool_name, tool_result)

            # Build tool message — use vision format if image is attached
            if tool_result.image_base64:
                image_mime_type = tool_result.image_mime_type or "image/jpeg"
                tool_content: Any = [
                    {"type": "text", "text": json.dumps(tool_result.to_dict(), ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": f"data:{image_mime_type};base64,{tool_result.image_base64}"}},
                ]
            else:
                tool_content = json.dumps(tool_result.to_dict(), ensure_ascii=False)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": tool_name,
                "content": tool_content,
            })

    else:
        if use_print:
            print(f"\n  {_c(YELLOW, f'⚠ Reached {config.max_steps} step limit')}")

    duration = time.time() - start_time
    emit({"event": "done", "success": success, "text": final_text, "duration": round(duration, 2)})
    if use_print:
        _print_final(success, final_text, duration)
    return success


def run_console_turn(
    user_input: str,
    config: ConsoleConfig,
    client: OpenAICompatClient,
    registry: ToolRegistry,
    system_prompt: str,
) -> bool:
    """Execute one user turn with live console output. Returns success."""
    return run_console_turn_evented(user_input, config, client, registry, system_prompt)


def run_console(config: ConsoleConfig) -> int:
    """Main console REPL loop."""
    _resolve_gui_backend(config)
    _print_banner(config)

    client = OpenAICompatClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )
    _, registry, _ = _build_registry(config)
    system_prompt = _get_system_prompt(config)

    while True:
        try:
            user_input = input(f"  {_c(BOLD + GREEN, '❯')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {_c(DIM, 'Goodbye.')}")
            return 0

        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            cmd = user_input.lower().split()
            if cmd[0] in ("/quit", "/exit", "/q"):
                print(f"  {_c(DIM, 'Goodbye.')}")
                return 0
            elif cmd[0] == "/clear":
                os.system("clear" if os.name == "posix" else "cls")
                _print_banner(config)
                continue
            elif cmd[0] == "/model":
                if len(cmd) > 1:
                    config = ConsoleConfig(
                        model=cmd[1],
                        base_url=config.base_url,
                        api_key=config.api_key,
                        gui_proxy_url=config.gui_proxy_url,
                        gui_model=config.gui_model,
                        gui_api_url=config.gui_api_url,
                        gui_api_key=config.gui_api_key,
                        gui_mode=config.gui_mode,
                        flat_gui_protocol=config.flat_gui_protocol,
                        serial=config.serial,
                        skill_paths=config.skill_paths,
                        max_steps=config.max_steps,
                        output_dir=config.output_dir,
                    )
                    client = OpenAICompatClient(
                        base_url=config.base_url,
                        api_key=config.api_key,
                        model=config.model,
                    )
                    print(f"  {_c(CYAN, f'Model → {config.model}')}")
                else:
                    print(f"  {_c(DIM, f'Current model: {config.model}')}")
                continue
            elif cmd[0] == "/status":
                gui_model, gui_api_url, _ = _resolve_gui_backend(config)
                print(f"  {_c(DIM, 'Model:')}  {config.model}")
                print(f"  {_c(DIM, 'GUI Model:')}  {gui_model}")
                print(f"  {_c(DIM, 'GUI Mode:')}  {config.gui_mode}")
                print(f"  {_c(DIM, 'API:')}    {config.base_url}")
                print(f"  {_c(DIM, 'GUI API:')} {gui_api_url}")
                print(f"  {_c(DIM, 'GUI:')}    {config.gui_proxy_url}")
                tools = [t["function"]["name"] for t in registry.to_openai_tools()]
                print(f"  {_c(DIM, 'Tools:')}  {', '.join(tools)}")
                continue
            else:
                print(f"  {_c(DIM, 'Unknown command. Try /quit /clear /model /status')}")
                continue

        # Execute the task
        run_console_turn(user_input, config, client, registry, system_prompt)

    return 0
