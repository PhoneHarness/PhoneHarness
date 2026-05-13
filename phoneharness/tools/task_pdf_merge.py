from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agent.message import PathRef, ToolResult
from ..backend import AdbBackend, LocalBackend, BackendCommandResult, BackendTimeoutError
from ._common import (
    extract_json_line,
    parse_path_ref,
    path_ref_schema,
    summarize_backend_steps,
    termux_path,
    termux_stage_relative,
    ubuntu_path,
    unique_suffix,
)
from .base import BaseTool


class TaskPdfMergeTool(BaseTool):
    name = "task_pdf_merge"
    description = "Merge a PDF inside Ubuntu with structured verification and a verified output artifact."
    input_schema = {
        "type": "object",
        "properties": {
            "source": path_ref_schema(description="Source PDF path."),
            "copies": {
                "type": "integer",
                "minimum": 2,
                "maximum": 10,
                "description": "How many copies to append when merging.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 900,
                "description": "Optional timeout in seconds.",
            },
        },
        "required": ["source"],
        "additionalProperties": False,
    }

    def __init__(self, *, backend: AdbBackend | LocalBackend | None = None, serial: str | None = None) -> None:
        self.backend = backend or AdbBackend()
        self.serial = serial
        self._on_device = isinstance(backend, LocalBackend)
        self.reset_execution_state()

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.reset_execution_state()
        source_ref = parse_path_ref(arguments.get("source"), field_name="source")
        copies = arguments.get("copies", 2)
        if not isinstance(copies, int) or copies < 2:
            self.set_error_type("tool_error")
            raise ValueError("task_pdf_merge.copies must be an integer >= 2")
        timeout = self._parse_timeout(arguments.get("timeout_seconds"))
        if source_ref.layer == "host" and not Path(source_ref.path).expanduser().resolve().is_file():
            self.set_error_type("tool_error")
            raise ValueError(f"source PDF does not exist: {source_ref.path}")

        suffix = unique_suffix()
        work_dir = ubuntu_path("pdf_merge", on_device=self._on_device)
        remote_output = ubuntu_path("pdf_merge", f"merged-{suffix}.pdf", on_device=self._on_device)
        remote_input = ubuntu_path("pdf_merge", f"input-{suffix}{Path(source_ref.path).suffix or '.pdf'}", on_device=self._on_device)
        steps: list[tuple[str, BackendCommandResult]] = []

        try:
            ensure_result = self.backend.ensure_ubuntu_dir(work_dir.path, serial=self.serial, timeout_seconds=timeout)
            self._record(steps, "ensure_ubuntu_dir", ensure_result)
            if not ensure_result.ok:
                self.set_error_type("backend_error")
                return ToolResult(
                    ok=False,
                    output=summarize_backend_steps(steps),
                    summary="task_pdf_merge failed while preparing the Ubuntu work directory",
                    exit_code=ensure_result.exit_code,
                    checks={"work_dir_ready": False},
                )
            prepared_input, source_ready = self._prepare_source(
                source_ref=source_ref,
                staged_input=remote_input,
                timeout=timeout,
                steps=steps,
            )
            code = self._build_code(
                input_path=prepared_input.path,
                output_path=remote_output.path,
                copies=copies,
            )
            python_result = self.backend.exec_ubuntu_python(code, serial=self.serial, timeout_seconds=timeout)
            self._record(steps, "exec_ubuntu_python", python_result)
            output_stat = self.backend.stat_ubuntu_path(remote_output.path, serial=self.serial, timeout_seconds=timeout)
            self._record(steps, "stat_ubuntu_path", output_stat)
        except BackendTimeoutError:
            self.set_error_type("timeout")
            raise

        payload = extract_json_line(python_result.stdout) if python_result.ok else {}
        source_pages = payload.get("source_pages") if isinstance(payload.get("source_pages"), int) else None
        merged_pages = payload.get("merged_pages") if isinstance(payload.get("merged_pages"), int) else None
        file_exists = bool(payload.get("file_exists"))
        checks = {
            "source_ready": source_ready,
            "python_ok": python_result.ok,
            "output_exists": output_stat.ok and file_exists,
            "page_count_matches": source_pages is not None and merged_pages == source_pages * copies,
        }
        ok = all(checks.values())
        if not ok:
            self.set_error_type("backend_error" if any(not result.ok for _, result in steps) else "tool_error")
        return ToolResult(
            ok=ok,
            output=summarize_backend_steps(steps),
            summary=(
                f"task_pdf_merge {'succeeded' if ok else 'failed'} with merged_pages={merged_pages}"
            ),
            exit_code=python_result.exit_code,
            artifact_paths=[remote_output] if checks["output_exists"] else [],
            checks=checks,
        )

    def _prepare_source(
        self,
        *,
        source_ref: PathRef,
        staged_input: PathRef,
        timeout: float | None,
        steps: list[tuple[str, BackendCommandResult]],
    ) -> tuple[PathRef, bool]:
        if source_ref.layer == "ubuntu":
            stat_result = self.backend.stat_ubuntu_path(source_ref.path, serial=self.serial, timeout_seconds=timeout)
            self._record(steps, "stat_ubuntu_source", stat_result)
            return source_ref, stat_result.ok

        if source_ref.layer == "termux":
            copy_result = self.backend.copy_termux_to_ubuntu(
                source_ref.path,
                staged_input.path,
                serial=self.serial,
                timeout_seconds=timeout,
            )
            self._record(steps, "copy_termux_to_ubuntu", copy_result)
            return staged_input, copy_result.ok

        stage_relative = termux_stage_relative("task-pdf-merge", Path(source_ref.path).name)
        stage_termux = termux_path(stage_relative)
        push_result = self.backend.push_termux_home(
            source_ref.path,
            stage_relative,
            serial=self.serial,
            timeout_seconds=timeout,
        )
        self._record(steps, "push_termux_home", push_result)
        copy_result = self.backend.copy_termux_to_ubuntu(
            stage_termux.path,
            staged_input.path,
            serial=self.serial,
            timeout_seconds=timeout,
        )
        self._record(steps, "copy_termux_to_ubuntu", copy_result)
        return staged_input, push_result.ok and copy_result.ok

    def _build_code(self, *, input_path: str, output_path: str, copies: int) -> str:
        return f"""
import json
from pathlib import Path
from pypdf import PdfReader, PdfWriter

input_path = {json.dumps(input_path)}
output_path = {json.dumps(output_path)}
copies = {copies}

reader = PdfReader(input_path)
source_pages = len(reader.pages)
writer = PdfWriter()
for _ in range(copies):
    writer.append(input_path)
with open(output_path, 'wb') as handle:
    writer.write(handle)
merged_reader = PdfReader(output_path)
print(json.dumps({{
    'input_path': input_path,
    'output_path': output_path,
    'source_pages': source_pages,
    'merged_pages': len(merged_reader.pages),
    'file_exists': Path(output_path).is_file(),
}}, ensure_ascii=False))
""".strip()

    def _parse_timeout(self, timeout_value: Any) -> float | None:
        if timeout_value is None:
            return None
        if not isinstance(timeout_value, (int, float)):
            self.set_error_type("tool_error")
            raise ValueError("task_pdf_merge.timeout_seconds must be numeric when provided")
        return float(timeout_value)

    def _record(self, steps: list[tuple[str, BackendCommandResult]], label: str, result: BackendCommandResult) -> None:
        steps.append((label, result))
        self.record_backend_run(label, result)
