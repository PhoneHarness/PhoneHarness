from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..backend import AdbBackend, LocalBackend
from ..skills import SkillLoaderError, inject_skill_prompt, load_skill_files
from ..task import update_summary_for_m3, write_journal
from ..tools.file_transfer import FileTransferTool
from ..tools.python_exec import PythonExecTool
from ..tools.registry import ToolRegistry
from ..tools.shell_exec import ShellExecTool
from ..tools.task_health_check import TaskHealthCheckTool
from ..tools.task_pdf_merge import TaskPdfMergeTool
from ..tools.task_word_append import TaskWordAppendTool
from .m2 import M2RunConfig, run_m2
from .message import ErrorType, PathLayer, PathRef


DEFAULT_ONDEVICE_SYSTEM_PROMPT = (
    "You are the PhoneHarness M3 on-device CLI controller. "
    "The controller process runs inside the Android device's Termux environment. "
    "All file paths on this device use layer='termux'. "
    "The Termux home directory is /data/data/com.termux/files/home. "
    "IMPORTANT: /tmp is NOT writable in Termux. Use $HOME or $TMPDIR for temp files. "
    "Stay on the CLI path only and do not invent GUI steps. "
    "For environment inspection or command execution tasks, use shell_exec instead of guessing. "
    "For file processing tasks (PDF merge, Word edit), use the task-specific tools (task_pdf_merge, task_word_append). "
    "When referencing files on this device, always use layer='termux' with the full absolute path. "
    "After each tool call, read ok, summary, exit_code, and output before answering. "
    "When the task is complete, answer briefly in Chinese with the command, stdout key lines, and exit_code."
)


@dataclass(frozen=True)
class M3RunConfig:
    user_input: str
    base_url: str
    api_key: str
    model: str
    output_dir: str | Path
    skill_paths: tuple[str, ...] = ()
    system_prompt: str = DEFAULT_ONDEVICE_SYSTEM_PROMPT
    max_steps: int = 6
    artifact_layer: PathLayer = "host"
    serial: str | None = None  # If set, use AdbBackend (host-driven); if None, use LocalBackend


@dataclass(frozen=True)
class M3RunOutcome:
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


def run_m3_ondevice(config: M3RunConfig) -> M3RunOutcome:
    skills = load_skill_files(config.skill_paths)
    injected_prompt = inject_skill_prompt(config.system_prompt, skills)

    # Choose backend based on whether we're running from host or on-device
    if config.serial:
        # Host-driven: model calls happen on host (direct network),
        # tool execution goes to device via adb run-as → proot Ubuntu (Channel 1)
        backend = AdbBackend()
        serial = config.serial
    else:
        # True on-device: controller runs in Termux native (has network).
        # Tools execute directly in Termux bash (use_proot=False).
        # NOTE: proot is blocked by SELinux untrusted_app_27 getcwd bug.
        # For M3 minimal tasks (pwd, whoami, python3 --version), Termux native is sufficient.
        # Future: bridge to Ubuntu proot for heavy tasks via a separate mechanism.
        backend = LocalBackend(workdir=Path.cwd(), use_proot=False)
        serial = None

    registry = ToolRegistry([
        ShellExecTool(backend=backend, serial=serial),
        PythonExecTool(backend=backend, serial=serial),
        TaskHealthCheckTool(backend=backend, serial=serial),
        TaskPdfMergeTool(backend=backend, serial=serial),
        TaskWordAppendTool(backend=backend, serial=serial),
        FileTransferTool(backend=backend, serial=serial),
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
            run_prefix="m3d",
            artifact_layer=config.artifact_layer,
        )
    )

    run_conclusion = (
        "M3 on-device controller spike completed on the current OpenAI-compatible model endpoint."
        if m2_outcome.success
        else "M3 on-device controller spike did not complete successfully."
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

    return M3RunOutcome(
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
    "DEFAULT_ONDEVICE_SYSTEM_PROMPT",
    "M3RunConfig",
    "M3RunOutcome",
    "SkillLoaderError",
    "run_m3_ondevice",
]
