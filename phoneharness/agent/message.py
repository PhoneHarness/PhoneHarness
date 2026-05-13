from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


PathLayer = Literal["host", "termux", "ubuntu"]
ErrorType = Literal["backend_error", "tool_error", "model_error", "timeout"]
RunStatus = Literal["running", "completed", "failed"]
StepType = Literal["cli", "gui"]
ToolCallStatus = Literal["completed", "failed"]


@dataclass(frozen=True)
class PathRef:
    layer: PathLayer
    path: str

    def __post_init__(self) -> None:
        if not Path(self.path).is_absolute():
            raise ValueError(f"PathRef.path must be absolute: {self.path}")

    def to_dict(self) -> dict[str, str]:
        return {"layer": self.layer, "path": self.path}


@dataclass
class ToolResult:
    ok: bool
    output: str
    summary: str
    exit_code: int | None = None
    artifact_paths: list[PathRef] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    # Optional Android intent descriptor — when present, the client app should
    # launch this intent from the foreground instead of relying on server-side
    # `am start` (which is blocked by Android API 36 BAL restrictions).
    intent: dict[str, Any] | None = None
    # Optional base64-encoded image (e.g. from gui_screenshot) to send to vision model.
    image_base64: str | None = None
    image_mime_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "output": self.output,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "artifact_paths": [ref.to_dict() for ref in self.artifact_paths],
            "checks": dict(self.checks),
        }
        if self.intent is not None:
            d["intent"] = self.intent
        if self.image_mime_type is not None:
            d["image_mime_type"] = self.image_mime_type
        return d


@dataclass
class ArtifactRecord:
    path_ref: PathRef
    type: str
    source_step: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_ref": self.path_ref.to_dict(),
            "type": self.type,
            "source_step": self.source_step,
        }


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    status: ToolCallStatus
    result_summary: str = ""
    result_ok: bool | None = None
    error_type: ErrorType | None = None
    tool_call_path: PathRef | None = None
    tool_result_path: PathRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "status": self.status,
            "result_summary": self.result_summary,
            "result_ok": self.result_ok,
            "error_type": self.error_type,
            "tool_call_path": None if self.tool_call_path is None else self.tool_call_path.to_dict(),
            "tool_result_path": None if self.tool_result_path is None else self.tool_result_path.to_dict(),
        }


@dataclass
class StepRecord:
    run_id: str
    index: int
    step_type: StepType
    decided_by: str
    assistant_text: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    request_path: PathRef | None = None
    response_path: PathRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "index": self.index,
            "step_type": self.step_type,
            "decided_by": self.decided_by,
            "assistant_text": self.assistant_text,
            "tool_calls": [tool_call.to_dict() for tool_call in self.tool_calls],
            "request_path": None if self.request_path is None else self.request_path.to_dict(),
            "response_path": None if self.response_path is None else self.response_path.to_dict(),
        }


@dataclass
class RunRecord:
    id: str
    user_input: str
    status: RunStatus
    control_provider: str
    control_model: str
    gui_provider: str | None = None
    gui_model: str | None = None
    active_skills: list[str] = field(default_factory=list)
    final_text: str = ""
    error_type: ErrorType | None = None
    steps: list[StepRecord] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_input": self.user_input,
            "status": self.status,
            "control_provider": self.control_provider,
            "control_model": self.control_model,
            "gui_provider": self.gui_provider,
            "gui_model": self.gui_model,
            "active_skills": list(self.active_skills),
            "final_text": self.final_text,
            "error_type": self.error_type,
            "steps": [step.to_dict() for step in self.steps],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }
