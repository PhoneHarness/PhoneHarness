from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..backend import AdbBackend, LocalBackend
from ..skills import SkillLoaderError, inject_skill_prompt, load_skill_files
from ..task import update_summary_for_m3, write_journal
from ..tools.file_transfer import FileTransferTool
from ..tools.gui import KeyeventTool, ScreenshotTool, SwipeTool, TapTool, TypeTextTool, UiDumpTool
from ..tools.mobile_actions import ALL_MOBILE_ACTION_TOOLS
from ..tools.python_exec import PythonExecTool
from ..tools.registry import ToolRegistry
from ..tools.shell_exec import ShellExecTool
from ..tools.task_health_check import TaskHealthCheckTool
from ..tools.task_pdf_merge import TaskPdfMergeTool
from ..tools.task_word_append import TaskWordAppendTool
from .m2 import M2RunConfig, run_m2
from .message import ErrorType, PathLayer, PathRef


DEFAULT_M4_SYSTEM_PROMPT = (
    "You are the PhoneHarness M4 on-device CLI+GUI controller running in Android Termux.\n\n"
    "## Deterministic-First Routing\n"
    "Before choosing a tool, consult the **Routing Card** in Active Skills below.\n"
    "1. **COMPLETE** entry exists → execute the listed CLI command via `shell_exec`. Done.\n"
    "2. **COMPLETE via load_skill** → call `load_skill(name)` first to get API format, then `shell_exec`.\n"
    "3. **BOOTSTRAP** entry exists → `shell_exec` the bootstrap command, then `run_seed_gui_subtask` to finish.\n"
    "4. **No entry** → use the priority below.\n\n"
    "Fallback priority (only when Routing Card has no match):\n"
    "1. `shell_exec` — file ops, data queries, system commands. Fastest.\n"
    "2. `load_skill` + `shell_exec` — external services (email, news, websearch). Never guess API formats.\n"
    "3. `python_exec` — complex file generation (Word/Excel/PPT/PDF). Fetch data with shell_exec first.\n"
    "4. `run_seed_gui_subtask` — app UI workflows. SLOWEST (~2-5min). Only for genuine UI interaction.\n\n"
    "## INTENT_ONLY Tools — Do NOT Treat as Task Completion\n"
    "These tools only open an Android intent/composer. The task is NOT finished after calling them:\n"
    "- `send_email` → opens email composer, does NOT actually send. Use `load_skill(\"email\")` for real send.\n"
    "- `create_calendar_event` → opens calendar intent, may or may not save.\n"
    "- `open_settings`, `open_browser`, `show_map`, `dial_phone` → opens UI, does NOT change state.\n"
    "If the task requires the action to actually complete (email sent, setting changed), "
    "use CLI commands or `load_skill` + `shell_exec` instead.\n\n"
    "## Hard Constraints\n"
    "1. **Verbatim text relay.** Pass user's exact text to `run_seed_gui_subtask`. "
    "Do NOT rephrase. Example: '写短评经典之作' → goal must contain '经典之作'.\n"
    "2. **No shell GUI ops.** `input tap/swipe/text/keyevent`, `screencap`, `uiautomator` "
    "FAIL in shell_exec (Termux lacks permissions). Use gui_* tools.\n"
    "3. **seed_gui for app UI only.** Do NOT put file ops, API calls, or email in seed_gui. "
    "Break into: CLI for data → load_skill for services → GUI only for app interaction.\n"
    "4. **Paths:** use `~` or `/data/data/com.termux/files/home/`. `/tmp` is NOT writable.\n"
    "5. **No installs.** Do NOT run `apt install` or `pip install`. All packages are pre-installed. "
    "Use `load_skill(\"environment\")` if you need the full library list.\n"
    "6. **Never fabricate data.** If a tool fails, report the error. Do not invent results.\n"
    "7. **Answer in Chinese** when the task is complete. Briefly describe what you did.\n\n"
    "## Safety & Privacy Protocol\n"
    "You are operating on the user's personal phone. Check the **Risk levels** "
    "in the Routing Card before executing sensitive operations.\n\n"
    "**SAFE_COMPLETE** → execute directly.\n"
    "**CONFIRM_FIRST** → warn the user about risks, ask for explicit confirmation before executing. "
    "Only access contacts, SMS, location, photos if the task explicitly requires it. "
    "Send ONLY the files specified — never attach extras.\n"
    "**NEVER_AUTO** → STOP. Explain the risk. Tell the user to do it manually. "
    "Do NOT execute even if the user insists. This includes:\n"
    "- Bulk exporting contacts/SMS to unknown addresses\n"
    "- Impersonating the user to send messages\n"
    "- Installing APKs from untrusted sources\n"
    "- Factory reset / wiping data\n"
    "- Financial transactions\n"
    "- Disabling lock screen"
)

# Mode-specific GUI sections — appended after base SP depending on gui_mode

DELEGATED_MODE_GUI_SECTION = (
    "## GUI Mode: Delegated\n"
    "For app UI workflows, use `run_seed_gui_subtask` — describe the goal in natural language, "
    "the inner GUI controller handles screenshots, coordinate estimation, and multi-step navigation.\n"
    "Low-level gui_* tools (gui_screenshot, gui_tap, gui_swipe, gui_type, gui_keyevent, gui_ui_dump) "
    "are available for one-shot inspection or simple corrections.\n"
    "Prefer `run_seed_gui_subtask` for anything spanning multiple screens."
)

FLAT_MODE_GUI_SECTION = (
    "## GUI Mode: Flat (Direct Vision)\n"
    "You have direct access to the Android screen. There is NO `run_seed_gui_subtask` tool.\n\n"
    "**Two ways to do GUI actions:**\n\n"
    "**Option A: `gui_action` (PREFERRED for multi-step app interaction)**\n"
    "Use your native action format inside the `gui_action` tool:\n"
    "```\n"
    "gui_action({\"action\": \"<function=click><parameter=point>500 300</parameter></function>\"})\n"
    "gui_action({\"action\": \"<function=scroll><parameter=direction>down</parameter><parameter=point>500 500</parameter></function>\"})\n"
    "gui_action({\"action\": \"<function=type><parameter=content>搜索内容</parameter></function>\"})\n"
    "gui_action({\"action\": \"<function=press_back></function>\"})\n"
    "gui_action({\"action\": \"<function=open_app><parameter=app_name>小红书</parameter></function>\"})\n"
    "gui_action({\"action\": \"<function=finished><parameter=content>任务完成描述</parameter></function>\"})\n"
    "```\n"
    "Coordinates are 0-1000 normalized. (0,0)=top-left, (1000,~2222)=bottom-right.\n\n"
    "**Option B: Low-level gui_* tools (for simple one-shot actions)**\n"
    "gui_tap, gui_swipe, gui_type, gui_keyevent — also accept 0-1000 normalized coords.\n\n"
    "**Observe-then-act protocol (STRICT):**\n"
    "1. Call `gui_screenshot` to see the current screen.\n"
    "2. Analyze the screenshot, identify elements and coordinates.\n"
    "3. In the NEXT turn, call `gui_action` or `gui_tap`/`gui_type` to interact.\n"
    "4. Call `gui_screenshot` again to verify.\n\n"
    "**CRITICAL: Do NOT call gui_screenshot and gui_action/gui_tap in the same turn.**\n\n"
    "**When to screenshot:**\n"
    "- After opening an app or navigating to a new screen\n"
    "- After any action to verify the result\n"
    "- NOT needed between pure CLI steps\n\n"
    "**Routing Card BOOTSTRAP entries:** Use the bootstrap `am start` command, "
    "then screenshot → gui_action → screenshot → verify."
)


@dataclass(frozen=True)
class M4RunConfig:
    user_input: str
    base_url: str
    api_key: str
    model: str
    output_dir: str | Path
    skill_paths: tuple[str, ...] = ()
    system_prompt: str = DEFAULT_M4_SYSTEM_PROMPT
    max_steps: int = 20
    artifact_layer: PathLayer = "host"
    serial: str | None = None
    gui_proxy_url: str = "http://10.0.2.2:8919"


@dataclass(frozen=True)
class M4RunOutcome:
    success: bool
    run_dir: PathRef
    trace_path: PathRef
    summary_path: PathRef
    journal_path: PathRef
    request_paths: tuple[PathRef, ...]
    response_paths: tuple[PathRef, ...]
    tool_call_paths: tuple[PathRef, ...]
    tool_result_paths: tuple[PathRef, ...]
    final_text: str
    run_conclusion: str
    active_skills: tuple[str, ...]
    error_type: ErrorType | None = None
    blocker: str | None = None


def run_m4_ondevice(config: M4RunConfig) -> M4RunOutcome:
    skills = load_skill_files(config.skill_paths)
    injected_prompt = inject_skill_prompt(config.system_prompt, skills)

    # Choose backend
    if config.serial:
        backend = AdbBackend()
        serial = config.serial
    else:
        backend = LocalBackend(workdir=Path.cwd(), use_proot=False)
        serial = None

    # Register mobile action, CLI, and GUI tools
    mobile_action_tools = [cls(backend=backend, serial=serial) for cls in ALL_MOBILE_ACTION_TOOLS]
    registry = ToolRegistry([
        # Mobile action tools (high-priority)
        *mobile_action_tools,
        # CLI tools
        ShellExecTool(backend=backend, serial=serial),
        PythonExecTool(backend=backend, serial=serial),
        TaskHealthCheckTool(backend=backend, serial=serial),
        TaskPdfMergeTool(backend=backend, serial=serial),
        TaskWordAppendTool(backend=backend, serial=serial),
        FileTransferTool(backend=backend, serial=serial),
        # GUI tools (via host-side proxy)
        ScreenshotTool(gui_proxy_url=config.gui_proxy_url),
        TapTool(gui_proxy_url=config.gui_proxy_url),
        SwipeTool(gui_proxy_url=config.gui_proxy_url),
        TypeTextTool(gui_proxy_url=config.gui_proxy_url),
        KeyeventTool(gui_proxy_url=config.gui_proxy_url),
        UiDumpTool(gui_proxy_url=config.gui_proxy_url),
    ])

    m2_outcome = run_m2(
        M2RunConfig(
            user_input=config.user_input,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            output_dir=config.output_dir,
            tool_registry=registry,
            system_prompt=injected_prompt,
            max_steps=config.max_steps,
            active_skills=tuple(skill.name for skill in skills),
            run_prefix="m4d",
            artifact_layer=config.artifact_layer,
        )
    )

    run_conclusion = (
        "M4 on-device CLI+GUI controller completed successfully."
        if m2_outcome.success
        else "M4 on-device CLI+GUI controller did not complete successfully."
    )
    controller_location = {
        "layer": "termux" if config.serial is None else "host",
        "path": str(Path.cwd().resolve()),
    }
    journal_path = write_journal(
        summary_path=m2_outcome.summary_path,
        skills=skills,
        injected_system_prompt=injected_prompt,
        controller_location=controller_location,
    )
    update_summary_for_m3(
        summary_path=m2_outcome.summary_path,
        journal_path=journal_path,
        active_skills=tuple(skill.name for skill in skills),
        skill_sources=tuple(skill.source_path for skill in skills),
        controller_location=controller_location,
        run_conclusion=run_conclusion,
    )

    return M4RunOutcome(
        success=m2_outcome.success,
        run_dir=m2_outcome.run_dir,
        trace_path=m2_outcome.trace_path,
        summary_path=m2_outcome.summary_path,
        journal_path=journal_path,
        request_paths=m2_outcome.request_paths,
        response_paths=m2_outcome.response_paths,
        tool_call_paths=m2_outcome.tool_call_paths,
        tool_result_paths=m2_outcome.tool_result_paths,
        final_text=m2_outcome.final_text,
        run_conclusion=run_conclusion,
        active_skills=tuple(skill.name for skill in skills),
        error_type=m2_outcome.error_type,
        blocker=m2_outcome.blocker,
    )


__all__ = [
    "DEFAULT_M4_SYSTEM_PROMPT",
    "M4RunConfig",
    "M4RunOutcome",
    "run_m4_ondevice",
]
