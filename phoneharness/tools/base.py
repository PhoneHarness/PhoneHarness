from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..agent.message import ErrorType, ToolResult

if TYPE_CHECKING:
    from ..backend import BackendCommandResult


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def reset_execution_state(self) -> None:
        self._last_backend_runs: list[tuple[str, BackendCommandResult]] = []
        self._last_error_type: ErrorType | None = None

    def record_backend_run(self, label: str, result: BackendCommandResult) -> None:
        runs = list(getattr(self, "_last_backend_runs", []))
        runs.append((label, result))
        self._last_backend_runs = runs

    def set_error_type(self, error_type: ErrorType | None) -> None:
        self._last_error_type = error_type

    def get_last_backend_runs(self) -> list[tuple[str, BackendCommandResult]]:
        return list(getattr(self, "_last_backend_runs", []))

    def get_last_error_type(self) -> ErrorType | None:
        return getattr(self, "_last_error_type", None)

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
