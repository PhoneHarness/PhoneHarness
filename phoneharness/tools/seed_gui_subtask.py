"""High-level GUI subtask tool that wraps the verified seed_gui controller.

Allows the default tool_calling orchestrator to delegate complex GUI tasks
(multi-step navigation, form filling, etc.) to the Seed GUI controller,
similar to how Claude Code's AgentTool delegates to sub-agents.

The seed_gui controller runs inline (same process), using its own
Seed XML protocol, automatic screenshots, 0-1000 coordinates, etc.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..agent.message import PathRef, ToolResult
from .base import BaseTool


class SeedGUISubtaskTool(BaseTool):
    name = "run_seed_gui_subtask"
    description = (
        "Execute a complex GUI task on the Android device using the high-precision "
        "Seed GUI controller. Use this when you need to perform multi-step GUI "
        "interactions like: searching in an app, filling forms, navigating through "
        "multiple screens, tapping specific UI elements, scrolling, typing text, etc. "
        "This tool handles screenshot capture, coordinate estimation, and action "
        "execution automatically. You just describe the goal in natural language. "
        "Prefer this tool whenever the GUI task spans more than one screen change or "
        "requires a full app flow. You can call CLI or MCP-style tools before or after "
        "this tool in the same task. For simple one-shot GUI actions (single tap, single "
        "screenshot, single back/home), you can still use low-level gui_* tools directly."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Natural language description of the GUI task to perform. "
                               "Be specific about what app to open, what to search, "
                               "what screens to reach, what buttons to tap, what text to "
                               "enter, and what final state should be achieved.",
            },
            "max_steps": {
                "type": "integer",
                "description": "Maximum steps for the GUI task (default 25).",
                "default": 25,
            },
        },
        "required": ["goal"],
    }

    def __init__(self, *, gui_proxy_url: str = "http://10.0.2.2:8919",
                 gui_model: str | None = None,
                 gui_api_url: str | None = None,
                 gui_api_key: str | None = None):
        self._gui_proxy_url = gui_proxy_url
        self._gui_model = gui_model
        self._gui_api_url = gui_api_url
        self._gui_api_key = gui_api_key
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        goal = arguments.get("goal", "")
        max_steps = arguments.get("max_steps", 25)

        if not goal:
            return ToolResult(ok=False, output="goal is required", summary="no goal provided")

        # Collect events from the seed_gui controller
        events: list[dict] = []

        def collect_event(event: dict):
            events.append(event)

        try:
            from ..controllers.seed_gui import run_seed_gui_turn
            success = run_seed_gui_turn(
                user_input=goal,
                gui_proxy_url=self._gui_proxy_url,
                max_steps=max_steps,
                gui_model=self._gui_model,
                gui_api_url=self._gui_api_url,
                gui_api_key=self._gui_api_key,
                on_event=collect_event,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                output=f"seed_gui execution error: {exc}",
                summary=f"GUI subtask failed: {exc}",
            )

        # Extract summary from events
        steps = sum(1 for e in events if e.get("event") == "step")
        done_ev = next((e for e in events if e.get("event") == "done"), {})
        final_text = done_ev.get("text", "")
        tool_calls = [e for e in events if e.get("event") == "tool_call"]

        # Conservative ok override: detect failure narratives even when
        # controller reported success (e.g. black screen, app not found).
        if success and final_text:
            _fail_signals = ["黑屏", "无法正常", "无法打开", "无法启动", "无法加载",
                             "页面空白", "应用崩溃", "找不到应用"]
            if any(sig in final_text for sig in _fail_signals):
                success = False

        # Save structured nested trace as artifact
        artifact_paths: list[PathRef] = []
        try:
            termux_home = Path("/data/data/com.termux/files/home")
            if not termux_home.exists():
                termux_home = Path.home()
            traces_dir = Path(os.environ.get("PHONEHARNESS_TRACES_DIR")
                              or termux_home / "artifacts" / "seed_gui_traces")
            traces_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_goal = "".join(c if c.isalnum() or c in "-_" else "_" for c in goal[:40])
            trace_file = traces_dir / f"{ts}_{safe_goal}.ndjson"
            with open(trace_file, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            artifact_paths.append(PathRef(layer="termux", path=str(trace_file)))
        except Exception:
            pass  # non-fatal — observability is best-effort

        # Build summary
        action_log = []
        result_by_action = {
            (e.get("step"), e.get("name")): e
            for e in events
            if e.get("event") == "tool_result"
        }
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            step_n = tc.get("step", "?")
            result = result_by_action.get((step_n, name))
            suffix = ""
            if result:
                suffix = f" -> {result.get('summary', '')}"
            action_log.append(f"  step {step_n}: {name}({json.dumps(args, ensure_ascii=False)[:80]}){suffix}")

        errors = [e for e in events if e.get("event") == "error"]
        error_log = [f"  {e.get('message', '')[:100]}" for e in errors]

        output = (
            f"GUI subtask {'completed' if success else 'failed'}.\n"
            f"Steps: {steps}\n"
            f"Action trace:\n" + "\n".join(action_log) + "\n"
            f"Errors: {len(errors)}" + (f"\n" + "\n".join(error_log) if errors else "") + "\n"
            f"Result: {final_text[:500]}"
        )
        if artifact_paths:
            output += f"\nNested trace: {artifact_paths[0].path}"

        return ToolResult(
            ok=success,
            output=output,
            summary=f"GUI subtask {'ok' if success else 'failed'} ({steps} steps)",
            artifact_paths=artifact_paths,
        )
