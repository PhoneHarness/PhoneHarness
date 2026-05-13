from __future__ import annotations

from typing import Any

from ..agent.message import ToolResult
from ..backend import AdbBackend, LocalBackend, BackendTimeoutError
from ._common import sanitize_stream
from .base import BaseTool


class TaskHealthCheckTool(BaseTool):
    name = "task_health_check"
    description = "Run the existing end-to-end device health check and return structured verification signals."
    input_schema = {
        "type": "object",
        "properties": {
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 900,
                "description": "Optional timeout in seconds.",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, *, backend: AdbBackend | LocalBackend | None = None, serial: str | None = None) -> None:
        self.backend = backend or AdbBackend()
        self.serial = serial
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        timeout_seconds = arguments.get("timeout_seconds")
        if timeout_seconds is not None and not isinstance(timeout_seconds, (int, float)):
            self.set_error_type("tool_error")
            raise ValueError("task_health_check.timeout_seconds must be numeric when provided")

        try:
            result = self.backend.health_check(
                serial=self.serial,
                timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            )
        except BackendTimeoutError:
            self.set_error_type("timeout")
            raise
        self.record_backend_run("health_check", result)
        if not result.ok:
            self.set_error_type("backend_error")

        stdout = sanitize_stream(result.stdout)
        checks = {
            "exit_code_zero": result.ok,
            "termux_section_present": "[termux]" in stdout,
            "ubuntu_section_present": "[ubuntu]" in stdout,
            "venv_section_present": "[clawmobile-venv]" in stdout,
            "python_visible": "Python " in stdout,
        }
        return ToolResult(
            ok=all(checks.values()),
            output=stdout,
            summary=(
                f"task_health_check {'passed' if all(checks.values()) else 'failed'} with exit_code={result.exit_code}"
            ),
            exit_code=result.exit_code,
            checks=checks,
        )
