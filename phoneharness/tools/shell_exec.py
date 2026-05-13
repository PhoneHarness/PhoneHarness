from __future__ import annotations

import os
import re
from typing import Any

from ..agent.message import ToolResult
from ..backend import AdbBackend, LocalBackend, BackendCommandResult, BackendTimeoutError
from .base import BaseTool


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])")

_REAL_APP_BOOTSTRAP_REWRITES = {
    "com.phoneuse.mxiaohongshu": "com.xingin.xhs",
    "com.phoneuse.mmeituan_waimai": "com.sankuai.meituan.takeoutnew",
    "com.phoneuse.mbilibili": "tv.danmaku.bili",
    "com.phoneuse.mdouban": "com.douban.frodo",
}


class ShellExecTool(BaseTool):
    name = "shell_exec"
    description = (
        "Execute a shell command in the device Termux/Linux environment. "
        "Use for: file operations (ls, cat, cp, mv, rm), text processing (grep, awk, sed), "
        "Python execution, package management (pip, pkg), system info (uname, df, free), "
        "and launching Android activities (am start). "
        "IMPORTANT: Do NOT use shell_exec for Android GUI operations. "
        "Never call 'input tap', 'input swipe', 'input text', 'input keyevent', "
        "'screencap', or 'uiautomator' via shell_exec — these require system permissions "
        "that shell_exec does not have. Instead use the dedicated GUI tools: "
        "gui_tap for tapping, gui_ui_dump for reading the screen, gui_screenshot for screenshots."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command to execute. Use absolute paths when possible. "
                    "For file redirection, prefer 'python3 -c' over bare 'echo > file' "
                    "to avoid shell quoting issues."
                ),
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self, *, backend: AdbBackend | LocalBackend | None = None, serial: str | None = None) -> None:
        self.backend = backend or AdbBackend()
        self.serial = serial
        self.last_backend_result: BackendCommandResult | None = None
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            self.set_error_type("tool_error")
            raise ValueError("shell_exec requires a non-empty string field `command`")

        command = _rewrite_real_app_bootstrap(command.strip())
        try:
            backend_result = self.backend.exec_ubuntu(command, serial=self.serial)
        except BackendTimeoutError:
            self.set_error_type("timeout")
            raise
        self.last_backend_result = backend_result
        self.record_backend_run("shell_exec", backend_result)
        ok = backend_result.exit_code == 0
        if not ok:
            self.set_error_type("backend_error")

        return ToolResult(
            ok=ok,
            output=_format_backend_result(backend_result),
            summary=(
                f"shell_exec {'succeeded' if ok else 'failed'} with exit_code={backend_result.exit_code} "
                f"for command: {command}"
            ),
            exit_code=backend_result.exit_code,
            checks={
                "exit_code_zero": ok,
                "stdout_captured": bool(backend_result.stdout.strip()),
            },
        )


def _format_backend_result(result: BackendCommandResult) -> str:
    stdout = _sanitize_stream(result.stdout)
    stderr = _sanitize_stream(result.stderr)
    lines = [
        f"exit_code: {result.exit_code}",
        "stdout:",
        stdout if stdout else "<empty>",
        "stderr:",
        stderr if stderr else "<empty>",
    ]
    return "\n".join(lines)


def _rewrite_real_app_bootstrap(command: str) -> str:
    real_app_mode = os.environ.get("PHONEHARNESS_REAL_APP_MODE")
    if real_app_mode != "1":
        return command
    gui_proxy_url = (os.environ.get("PHONEHARNESS_GUI_PROXY_URL") or "http://127.0.0.1:8919").rstrip("/")
    for mock_pkg, real_pkg in _REAL_APP_BOOTSTRAP_REWRITES.items():
        if mock_pkg in command:
            return f'curl -s "{gui_proxy_url}/launch?package={real_pkg}"'
    return command


def _sanitize_stream(text: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    return cleaned.rstrip("\n")
