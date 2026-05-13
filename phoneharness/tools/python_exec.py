from __future__ import annotations

from typing import Any

from ..agent.message import ToolResult
from ..backend import AdbBackend, LocalBackend, BackendTimeoutError
from ._common import sanitize_stream, summarize_backend_output
from .base import BaseTool


class PythonExecTool(BaseTool):
    name = "python_exec"
    description = "Execute a Python snippet inside the device Ubuntu clawmobile virtualenv."
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute inside the device Ubuntu clawmobile environment.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 600,
                "description": "Optional execution timeout in seconds.",
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    }

    def __init__(self, *, backend: AdbBackend | LocalBackend | None = None, serial: str | None = None) -> None:
        self.backend = backend or AdbBackend()
        self.serial = serial
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        code = arguments.get("code")
        timeout_seconds = arguments.get("timeout_seconds")
        if not isinstance(code, str) or not code.strip():
            self.set_error_type("tool_error")
            raise ValueError("python_exec requires a non-empty string field `code`")
        if timeout_seconds is not None and not isinstance(timeout_seconds, (int, float)):
            self.set_error_type("tool_error")
            raise ValueError("python_exec.timeout_seconds must be numeric when provided")

        try:
            result = self.backend.exec_ubuntu_python(
                code,
                serial=self.serial,
                timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            )
        except BackendTimeoutError:
            self.set_error_type("timeout")
            raise
        self.record_backend_run("python_exec", result)
        if not result.ok:
            self.set_error_type("backend_error")

        clean_stdout = sanitize_stream(result.stdout)
        checks = {
            "exit_code_zero": result.ok,
            "stdout_captured": bool(clean_stdout),
        }
        return ToolResult(
            ok=result.ok,
            output=summarize_backend_output(result.stdout, result.stderr),
            summary=(
                f"python_exec {'succeeded' if result.ok else 'failed'} with exit_code={result.exit_code}"
            ),
            exit_code=result.exit_code,
            checks=checks,
        )
