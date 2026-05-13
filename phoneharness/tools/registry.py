from __future__ import annotations

import json
from typing import Any

from ..agent.message import ErrorType, ToolResult
from .base import BaseTool


class ToolRegistry:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def to_openai_tools(self) -> list[dict[str, Any]]:
        tools = [tool.to_openai_tool() for tool in self._tools.values()]
        # Strip "additionalProperties" from schemas — Gemini API rejects it
        for tool in tools:
            params = tool.get("function", {}).get("parameters", {})
            self._strip_additional_properties(params)
        return tools

    @staticmethod
    def _strip_additional_properties(schema: dict) -> None:
        """Recursively remove additionalProperties from a JSON schema dict."""
        schema.pop("additionalProperties", None)
        for v in schema.get("properties", {}).values():
            if isinstance(v, dict):
                ToolRegistry._strip_additional_properties(v)
        items = schema.get("items")
        if isinstance(items, dict):
            ToolRegistry._strip_additional_properties(items)

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    def execute_tool_call(self, tool_call: dict[str, Any]) -> tuple[dict[str, Any], ToolResult]:
        _arguments, result, _, _ = self.execute_tool_call_detailed(tool_call)
        return _arguments, result

    def execute_tool_call_detailed(
        self,
        tool_call: dict[str, Any],
    ) -> tuple[dict[str, Any], ToolResult, list[tuple[str, Any]], ErrorType | None]:
        function = tool_call.get("function") or {}
        name = function.get("name")
        if not name or name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}")
        arguments_text = function.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tool arguments for {name}: {arguments_text}") from exc

        tool = self._tools[name]
        tool.reset_execution_state()
        result = tool.execute(arguments)
        backend_runs = tool.get_last_backend_runs()
        error_type = tool.get_last_error_type()
        return arguments, result, backend_runs, error_type
