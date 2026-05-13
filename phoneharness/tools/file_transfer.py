from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agent.message import PathRef, ToolResult
from ..backend import AdbBackend, LocalBackend, BackendCommandResult, BackendTimeoutError
from ._common import (
    ensure_host_parent,
    parse_path_ref,
    path_ref_schema,
    summarize_backend_steps,
    termux_path,
    termux_relative_path,
    termux_stage_relative,
)
from .base import BaseTool


class FileTransferTool(BaseTool):
    name = "file_transfer"
    description = "Move files across host, Termux, and Ubuntu layers using typed PathRef arguments."
    input_schema = {
        "type": "object",
        "properties": {
            "source": path_ref_schema(description="Source file reference."),
            "dest": path_ref_schema(description="Destination file reference."),
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 600,
                "description": "Optional transfer timeout in seconds.",
            },
        },
        "required": ["source", "dest"],
        "additionalProperties": False,
    }

    def __init__(self, *, backend: AdbBackend | LocalBackend | None = None, serial: str | None = None) -> None:
        self.backend = backend or AdbBackend()
        self.serial = serial
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        source = parse_path_ref(arguments.get("source"), field_name="source")
        dest = parse_path_ref(arguments.get("dest"), field_name="dest")
        timeout = self._parse_timeout(arguments.get("timeout_seconds"))
        if source.layer == dest.layer:
            self.set_error_type("tool_error")
            raise ValueError("file_transfer requires source and dest to be in different layers")

        steps: list[tuple[str, BackendCommandResult]] = []
        artifact_paths: list[PathRef] = []
        checks: dict[str, bool] = {}
        exit_code: int | None = None

        try:
            if source.layer == "host" and dest.layer == "termux":
                result = self.backend.push_termux_home(
                    source.path,
                    termux_relative_path(dest),
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "push_termux_home", result)
                stat_result = self.backend.stat_termux_path(dest.path, serial=self.serial, timeout_seconds=timeout)
                self._record(steps, "stat_termux_path", stat_result)
                checks = {
                    "source_exists": self._host_exists(source),
                    "transfer_ok": result.ok,
                    "dest_exists": stat_result.ok,
                }
                exit_code = stat_result.exit_code
                artifact_paths = [dest] if stat_result.ok else []
            elif source.layer == "termux" and dest.layer == "host":
                ensure_host_parent(dest)
                result = self.backend.pull_termux_home(
                    termux_relative_path(source),
                    dest.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "pull_termux_home", result)
                checks = {
                    "transfer_ok": result.ok,
                    "dest_exists": self._host_exists(dest),
                }
                exit_code = result.exit_code
                artifact_paths = [dest] if checks["dest_exists"] else []
            elif source.layer == "host" and dest.layer == "ubuntu":
                stage_relative = termux_stage_relative("file-transfer", Path(source.path).name)
                stage_termux = termux_path(stage_relative)
                push_result = self.backend.push_termux_home(
                    source.path,
                    stage_relative,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "push_termux_home", push_result)
                copy_result = self.backend.copy_termux_to_ubuntu(
                    stage_termux.path,
                    dest.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "copy_termux_to_ubuntu", copy_result)
                stat_result = self.backend.stat_ubuntu_path(dest.path, serial=self.serial, timeout_seconds=timeout)
                self._record(steps, "stat_ubuntu_path", stat_result)
                checks = {
                    "source_exists": self._host_exists(source),
                    "staged_to_termux": push_result.ok,
                    "copied_to_ubuntu": copy_result.ok,
                    "dest_exists": stat_result.ok,
                }
                exit_code = stat_result.exit_code
                artifact_paths = [dest] if stat_result.ok else []
            elif source.layer == "ubuntu" and dest.layer == "host":
                ensure_host_parent(dest)
                stage_relative = termux_stage_relative("file-transfer", Path(source.path).name)
                stage_termux = termux_path(stage_relative)
                copy_result = self.backend.copy_ubuntu_to_termux(
                    source.path,
                    stage_termux.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "copy_ubuntu_to_termux", copy_result)
                pull_result = self.backend.pull_termux_home(
                    stage_relative,
                    dest.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "pull_termux_home", pull_result)
                checks = {
                    "staged_to_termux": copy_result.ok,
                    "pulled_to_host": pull_result.ok,
                    "dest_exists": self._host_exists(dest),
                }
                exit_code = pull_result.exit_code
                artifact_paths = [dest] if checks["dest_exists"] else []
            elif source.layer == "termux" and dest.layer == "ubuntu":
                copy_result = self.backend.copy_termux_to_ubuntu(
                    source.path,
                    dest.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "copy_termux_to_ubuntu", copy_result)
                stat_result = self.backend.stat_ubuntu_path(dest.path, serial=self.serial, timeout_seconds=timeout)
                self._record(steps, "stat_ubuntu_path", stat_result)
                checks = {
                    "copy_ok": copy_result.ok,
                    "dest_exists": stat_result.ok,
                }
                exit_code = stat_result.exit_code
                artifact_paths = [dest] if stat_result.ok else []
            elif source.layer == "ubuntu" and dest.layer == "termux":
                copy_result = self.backend.copy_ubuntu_to_termux(
                    source.path,
                    dest.path,
                    serial=self.serial,
                    timeout_seconds=timeout,
                )
                self._record(steps, "copy_ubuntu_to_termux", copy_result)
                stat_result = self.backend.stat_termux_path(dest.path, serial=self.serial, timeout_seconds=timeout)
                self._record(steps, "stat_termux_path", stat_result)
                checks = {
                    "copy_ok": copy_result.ok,
                    "dest_exists": stat_result.ok,
                }
                exit_code = stat_result.exit_code
                artifact_paths = [dest] if stat_result.ok else []
            else:
                self.set_error_type("tool_error")
                raise ValueError(f"unsupported file_transfer path: {source.layer} -> {dest.layer}")
        except BackendTimeoutError:
            self.set_error_type("timeout")
            raise

        ok = all(checks.values())
        if not ok:
            self.set_error_type("backend_error")
        return ToolResult(
            ok=ok,
            output=summarize_backend_steps(steps),
            summary=(
                f"file_transfer {source.layer}->{dest.layer} {'succeeded' if ok else 'failed'}"
            ),
            exit_code=exit_code,
            artifact_paths=artifact_paths,
            checks=checks,
        )

    def _parse_timeout(self, timeout_value: Any) -> float | None:
        if timeout_value is None:
            return None
        if not isinstance(timeout_value, (int, float)):
            self.set_error_type("tool_error")
            raise ValueError("file_transfer.timeout_seconds must be numeric when provided")
        return float(timeout_value)

    def _record(self, steps: list[tuple[str, BackendCommandResult]], label: str, result: BackendCommandResult) -> None:
        steps.append((label, result))
        self.record_backend_run(label, result)

    def _host_exists(self, path_ref: PathRef) -> bool:
        return Path(path_ref.path).expanduser().resolve().is_file()
